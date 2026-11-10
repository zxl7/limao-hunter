#!/usr/bin/env bash
# 仅发布工作区源码与重新构建的前端；不运行迁移、依赖安装或调度脚本。
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="${LIMAO_SERVER:-ubuntu@43.136.183.186}"
KEY="${LIMAO_SSH_KEY:-$HOME/.ssh/id_ed25519_limao}"
ROOT="${LIMAO_REMOTE_ROOT:-/home/ubuntu/limao}"
FRONTEND="${LIMAO_FRONTEND:-$(dirname "$APP_DIR")/狸猫终端}"
EXECUTE=0
case "${1:-}" in
  '') ;;
  --execute) EXECUTE=1 ;;
  --help|-h)
    printf '%s\n' '用法：bash scripts/release_site.sh [--execute|--help]' \
      '默认 dry-run：仍重新构建和本地检查，但远端只读。--execute 备份、同步、重启并验证；失败回滚且非零退出。' \
      '环境覆盖：LIMAO_SERVER、LIMAO_SSH_KEY、LIMAO_REMOTE_ROOT、LIMAO_FRONTEND。' \
      '仅传 serve.py 及 common/scripts/wealth_code 内 .py/.sh 源码；配置、数据和其他文件不传。' \
      '备份位于远端 releases/code-<时间>-<随机值>/backup，保留旧 dist hash，不执行 rsync --delete。'
    exit 0 ;;
  *) printf '未知参数：%s\n' "$1" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || exit 2
# 远端路径限制同时避免 SSH/rsync 的远端 shell 参数注入。
[[ "$SERVER" =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$ ]] || exit 2
[[ "$ROOT" =~ ^/[a-zA-Z0-9_./-]+$ && "$ROOT" != *..* && "$ROOT" != / ]] || exit 2
for cmd in ssh rsync python3 npm; do command -v "$cmd" >/dev/null; done
[ -f "$KEY" ] && [ -d "$FRONTEND/node_modules" ] && [ -d "$APP_DIR/scripts" ]
# 即使白名单暂存规则扩展，传输层也始终拒绝这些文件。
EXCLUDES=(--exclude='*cache*' --exclude='.env*' --exclude='settings.json*' --exclude='ai_env.json*'
  --exclude='*.pem' --exclude='*.key' --exclude='*.p12' --exclude='*.pfx' --exclude='id_*'
  --exclude='.git/' --exclude='.workbuddy/' --exclude='node_modules/' --exclude='venv/'
  --exclude='.venv/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='config/' --exclude='configs/')
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15)
printf -v RSH 'ssh -i %q -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15' "$KEY"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/limao-release.XXXXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
mkdir "$TMP/payload"
printf '[release] 前端重新构建（输出到隔离临时目录）\n'
npm --prefix "$FRONTEND" run build -- --outDir "$TMP/payload/dist"
python3 - "$APP_DIR" "$TMP/payload" <<'PY'
import ast, os, shutil, sys
from pathlib import Path
root, stage = map(Path, sys.argv[1:])
blocked = {'.git', '.workbuddy', 'node_modules', 'venv', '.venv', '__pycache__', 'config', 'configs', 'logs', 'outputs'}
def forbidden(p):
    return any(x in blocked or x.lower().lstrip('.').startswith('cache') or x.startswith('.env')
               or x in {'settings.json', 'ai_env.json'} or x.startswith('id_')
               or x.endswith(('.pem', '.key', '.p12', '.pfx')) for x in p.parts)
files = [root / 'serve.py']
for name in ('common', 'scripts', 'wealth_code'):
    for base, dirs, names in os.walk(root / name, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not forbidden(Path(base, d).relative_to(root)) and not Path(base,d).is_symlink())
        files.extend(Path(base,n) for n in sorted(names) if Path(n).suffix in {'.py','.sh'})
for src in files:
    rel = src.relative_to(root)
    if forbidden(rel) or src.is_symlink():
        continue
    if src.suffix == '.py':
        ast.parse(src.read_text(encoding='utf-8'), filename=str(rel))
    dst = stage / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
print('[release] 源码语法检查通过；源码文件数', len(list(stage.rglob('*.py'))) + len(list(stage.rglob('*.sh'))))
PY
# 远端辅助程序只处理暂存白名单文件，备份和回滚均不遍历配置/缓存。
cat > "$TMP/deploy.py" <<'PY'
import ast, datetime, hashlib, json, os, re, shutil, signal, subprocess, sys, time, urllib.request
from pathlib import Path
import fcntl
root = Path(sys.argv[1])
release = Path(sys.argv[2]) if len(sys.argv) > 2 else None

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def service_paths():
    assert run('systemctl','is-active','limao-serve') == 'active'
    pid = run('systemctl','show','limao-serve','-p','MainPID','--value')
    env = dict(x.split(b'=',1) for x in Path('/proc',pid,'environ').read_bytes().split(b'\0') if b'=' in x)
    app = (root/'current').resolve(strict=True)
    assert Path(os.readlink('/proc/'+pid+'/cwd')).resolve() == app, '运行目录与 current 不符'
    dist = Path(env.get(b'LIMAO_DIST_DIR',str(app.parent/'狸猫终端/dist').encode()).decode()).resolve()
    assert dist == app/'dist', '服务 dist 不是 current/dist，拒绝部署'
    assert not env.get(b'SHARE_TOKEN'), '当前脚本要求本机健康端点无需 token；不读取配置密钥'
    port = int(env.get(b'SHARE_PORT',b'8000'))
    return app, dist, 'http://127.0.0.1:'+str(port)

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def get(base, path):
    with urllib.request.urlopen(base+path, timeout=10) as response:
        assert response.status == 200
        return response.read()

def health(base, expected=None):
    assert run('systemctl','is-active','limao-serve') == 'active'
    home = get(base,'/')
    if expected:
        assert hashlib.sha256(home).hexdigest() == digest(expected/'dist/index.html'), '首页不是本次构建'
    assets = sorted(set(re.findall(r'/assets/[^"\s<>]+',home.decode())))
    assert assets and any(p.endswith('.js') for p in assets), '首页无 JS hash 资产'
    for asset in assets:
        assert '..' not in asset
        raw = get(base,asset)
        if expected:
            assert hashlib.sha256(raw).hexdigest() == digest(expected/'dist'/asset.lstrip('/')), '资产 hash 不符'
    market = json.loads(get(base,'/api/market/latest'))
    assert isinstance(market,dict) and isinstance(market.get('ts'),str) and market['ts'] and 'error' not in market
    datetime.datetime.fromisoformat(market['ts'])
    status = json.loads(get(base,'/api/health'))
    assert isinstance(status.get('files'),list) and isinstance(status.get('data_root'),str)
    datetime.datetime.fromisoformat(status['ts'])
    entry = next(x for x in status['files'] if x['route']=='/api/market/latest')
    assert entry['exists'] is True and entry['mtime'] and entry['size'] > 0
    print(json.dumps({'assets':assets,'market_ts':market['ts'],'health_ts':status['ts'],
                      'market_mtime':entry['mtime'],'market_age_sec':entry['age_sec']},ensure_ascii=False),flush=True)

app, dist, base = service_paths()
if release is None:
    health(base)
    print('APP='+str(app),flush=True)
    sys.exit(0)
lock = open(root/'.release-site.lock','a')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert release.parent == root/'releases' and release.name.startswith('code-')
stage = release/'payload'
backup = release/'backup'
backup.mkdir(mode=0o700)
changes = []
for src in sorted(stage.rglob('*')):
    if not src.is_file():
        continue
    rel = src.relative_to(stage)
    assert not src.is_symlink()
    assert not any(x.startswith('.env') or x.lower().lstrip('.').startswith('cache') or x in
                   {'.git','.workbuddy','config','configs','node_modules','venv','.venv','__pycache__','settings.json','ai_env.json'} for x in rel.parts)
    assert rel.parts[0] == 'dist' or str(rel)=='serve.py' or (rel.parts[0] in {'common','scripts','wealth_code'} and src.suffix in {'.py','.sh'})
    if src.suffix == '.py':
        ast.parse(src.read_text(encoding='utf-8'), filename=str(rel))
    elif src.suffix == '.sh':
        subprocess.run(['bash','-n',str(src)],check=True)
    dst = app/rel
    assert dst.resolve().is_relative_to(app), '目标路径越界'
    assert not dst.is_symlink(), '拒绝覆盖软链接'
    if dst.exists():
        assert dst.is_file()
        if digest(src)==digest(dst) and (src.stat().st_mode & 0o777)==(dst.stat().st_mode & 0o777):
            continue
        saved = backup/rel
        saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(dst,saved)
    changes.append({'path':str(rel),'existed':dst.exists(),'old_sha256':digest(dst) if dst.exists() else None,'new_sha256':digest(src)})
(release/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2))
print('BACKUP='+str(backup)+' CHANGED='+str(len(changes)),flush=True)
def replace(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    tmp = dst.with_name(dst.name+'.release-site-tmp')
    assert not tmp.exists()
    shutil.copy2(src,tmp)
    os.replace(tmp,dst)
def interrupted(signum,frame):
    raise RuntimeError('部署被信号中断 '+str(signum))
for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
    signal.signal(sig,interrupted)
applied = []
try:
    # index.html 最后切换，避免首页引用尚未上传的资产。
    for item in sorted(changes,key=lambda x:x['path']=='dist/index.html'):
        dst = app/item['path']
        assert (digest(dst) if dst.exists() else None) == item['old_sha256'], '发现备份后的并行修改，拒绝覆盖 '+str(dst)
        applied.append(item)
        replace(stage/item['path'],dst)
    subprocess.run(['systemctl','restart','limao-serve'],check=True,timeout=60)
    deadline = time.monotonic()+40
    while True:
        try:
            health(base,stage)
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            # 有界等待服务就绪，不重试重启或部署命令。
            time.sleep(1)
    print('RELEASE_OK '+str(release),flush=True)
except BaseException:
    print('发布失败，恢复本次文件',file=sys.stderr,flush=True)
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
        signal.signal(sig,signal.SIG_IGN)
    errors=[]
    for item in reversed(applied):
        try:
            dst=app/item['path']
            if dst.exists() and digest(dst) not in {item['new_sha256'], digest(backup/item['path']) if item['existed'] else ''}:
                raise RuntimeError('发现并行修改，不覆盖 '+str(dst))
            if item['existed']:
                replace(backup/item['path'],dst)
            elif dst.exists():
                dst.unlink()
        except Exception as exc:
            errors.append(str(exc))
    try:
        subprocess.run(['systemctl','restart','limao-serve'],check=True,timeout=60)
        deadline=time.monotonic()+40
        while True:
            try:
                health(base)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
    except Exception as exc:
        errors.append(str(exc))
    print('ROLLBACK_FAILED '+repr(errors) if errors else 'ROLLBACK_OK',file=sys.stderr,flush=True)
    raise
PY
printf '[release] 只读远端预检\n'
"${SSH[@]}" "$SERVER" "sudo -n python3 - '$ROOT'" < "$TMP/deploy.py"
# --checksum 防止时间戳相同的工作区内容被遗漏；不带 --delete。
if [ "$EXECUTE" = 0 ]; then
  printf '[release] dry-run：不上传、不备份、不重启\n'
  rsync -rcni --omit-dir-times "${EXCLUDES[@]}" -e "$RSH" "$TMP/payload/" "$SERVER:$ROOT/current/"
  exit 0
fi
RELEASE="$ROOT/releases/code-$(date -u +%Y%m%dT%H%M%SZ)-${TMP##*.}"
"${SSH[@]}" "$SERVER" "set -eu; test -d '$ROOT/releases'; mkdir -m 700 '$RELEASE'; mkdir '$RELEASE/payload'"
rsync -rct --perms --omit-dir-times "${EXCLUDES[@]}" -e "$RSH" "$TMP/payload/" "$SERVER:$RELEASE/payload/"
# 辅助程序只走标准输入，远端快照不复制任何配置或凭证。
"${SSH[@]}" "$SERVER" "sudo -n python3 - '$ROOT' '$RELEASE'" < "$TMP/deploy.py"
printf '[release] 发布完成，备份：%s/backup\n' "$RELEASE"
