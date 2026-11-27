#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-shell.py 的反证：每条判据都必须能在真实样本上失败。

判据外置、真闸门 subprocess 跑，不用「照抄一份逻辑」的镜像验证 ——
镜像只证明自己理解自洽，对真闸门零约束力。

用例三组：
  破坏样本（GNU-only 写法）  → rc=1 且命中规则码精确等于预期
  良性样本（双兼容写法）     → rc=0（证明闸门不误伤）
  磁盘自证                   → 真实仓库文件 sha256 前后一致
"""
import hashlib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "check-shell.py")
PY = sys.executable or "python3"


def run_gate_in(tmp):
    """在临时目录里跑真闸门，返回 (rc, stdout)。"""
    r = subprocess.run([PY, GATE, tmp], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def write(tmp, rel, text):
    p = os.path.join(tmp, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


def main():
    # ---- 磁盘自证基线：整个仓库的 shell/yaml 文件 ----
    tracked = []
    for dirpath, dirnames, filenames in os.walk(os.path.dirname(HERE)):
        dirnames[:] = [d for d in dirnames
                       if d not in {'.git', '.build', '.verify', 'node_modules',
                                    '__pycache__', 'patches'}]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in {'.sh', '.bash', '.yml', '.yaml'}:
                tracked.append(os.path.join(dirpath, name))
    before = {p: hashlib.sha256(open(p, 'rb').read()).hexdigest() for p in tracked}

    cases = [
        # ---- 破坏样本：GNU-only 原地编辑（run 44 真事故的两行原样）----
        ("S1 mkdeb run-44 原样（GNU sed 原地编辑）",
         "devkit/mkdeb.sh",
         '#!/bin/bash\nsed -i "s|__HELPER__|/usr/bin/oka-iconinject|g" '
         '"$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm"\n',
         {"R8"}),
        ("S2 sed -i 带 backup 后缀（GNU 写法、BSD 语义也不对）",
         "devkit/x.sh", '#!/bin/bash\nsed -i.bak "s|a|b|" f.txt\n',
         {"R8"}),
        ("S3 --in-place 长选项",
         "devkit/y.sh", '#!/bin/bash\nsed --in-place "s|a|b|" f.txt\n',
         {"R8"}),
        # ---- 破坏样本：R1 历史事故形态（bash 3.2 变量名吞并）----
        ("S4 $CPU 后紧跟中文全角逗号（run 42-3 历史形态）",
         "devkit/z.sh", '#!/bin/bash\nset -u\necho "✓ $CPU，单切片"\n',
         {"R1"}),
        # ---- 良性样本：必须放过 ----
        ("S5 sub_file 双兼容替换（mkdeb.sh 现行写法）",
         "devkit/mkdeb.sh",
         '#!/bin/bash\nsub_file() {\n  _script="$1"; shift\n'
         '  for _f in "$@"; do\n    _tmp="${_f}.oka-tmp"\n'
         '    sed "$_script" "$_f" > "$_tmp" || { rm -f "$_tmp"; return 1; }\n'
         '    mv "$_tmp" "$_f"\n  done\n}\n'
         'sub_file "s|__HELPER__|/usr/bin/oka-iconinject|g" "$STAGE/DEBIAN/postinst"\n',
         None),
        ("S6 ${CPU} 花括号写法（R1 的安全形态）",
         "devkit/w.sh", '#!/bin/bash\nset -u\necho "✓ ${CPU}，单切片"\n',
         None),
        ("S7 管道里的普通 sed（无原地编辑）",
         "devkit/v.sh", '#!/bin/bash\nxcrun lipo -info d.dylib | sed \'s/.*: *//\'\n',
         None),
    ]

    bad = 0
    for name, rel, text, expect in cases:
        with tempfile.TemporaryDirectory(prefix="shellgate-") as tmp:
            write(tmp, rel, text)
            rc, out = run_gate_in(tmp)
            if expect is None:
                if rc != 0:
                    print("  ✗ %s：良性样本被误伤（rc=%d）\n%s" % (name, rc, out))
                    bad += 1
                else:
                    print("  ✓ %s → 放过" % name)
                continue
            # 破坏样本：rc=1 且规则码命中集合精确等于预期
            got = set()
            for line in out.splitlines():
                # 命中行形如：  devkit/x.sh:2:11  [R8]  ...
                if "  [R" in line and "]" in line:
                    code = line.split("[", 1)[1].split("]", 1)[0]
                    if code.startswith("R"):
                        got.add(code)
            if rc != 1:
                print("  ✗ %s：闸门 rc=%d（应为 1）——漏网\n%s" % (name, rc, out))
                bad += 1
            elif got != expect:
                print("  ✗ %s：规则码不符 预期 %s 实际 %s" % (name, sorted(expect), sorted(got)))
                bad += 1
            else:
                print("  ✓ %-34s → %s" % (name, "/".join(sorted(got))))

    # ---- 磁盘自证 ----
    after = {p: hashlib.sha256(open(p, 'rb').read()).hexdigest() for p in tracked}
    if before != after:
        print("  ✗ 仓库文件被改动（sha256 前后不一致）")
        bad += 1
    else:
        print("  ✓ 仓库 %d 个 shell/yaml 文件 sha256 前后一致" % len(tracked))

    if bad:
        print("selftest-shell: FAIL（%d 个用例不符）" % bad)
        return 1
    print("selftest-shell: PASS（%d 个用例全部符合预期：破坏样本精确触发、良性放过、磁盘未动）"
          % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())