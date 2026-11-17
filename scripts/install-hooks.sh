#!/usr/bin/env bash
# Installs a pre-commit hook that runs the privacy scanner on the files about to be committed.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
cat > "$root/.git/hooks/pre-commit" <<'HOOK'
#!/usr/bin/env bash
set -e
root="$(git rev-parse --show-toplevel)"
mapfile -d '' files < <(git diff --cached --name-only -z --diff-filter=ACMR)
[ ${#files[@]} -eq 0 ] && exit 0
python3 "$root/scripts/check_public.py" "${files[@]}"
HOOK
chmod +x "$root/.git/hooks/pre-commit"
echo "pre-commit hook installed"
