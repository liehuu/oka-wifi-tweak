#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""github.com 被代理 502 阻断时，经 api.github.com 的 Git Data API 等价推送。

只做一件事：把本地 main 领先 origin/main 的那一个 commit（blob/tree/commit）
按同样内容重建到远端，然后 fast-forward 更新 refs/heads/main。

前置条件（缺一即退出，不猜）：
  - 本地 HEAD 相对远端 main 只允许两种 FF 形态：
      (a) 本地 parent == 远端 main（标准单 commit 领先）；
      (b) 本地 parent 与远端 main 内容同树（API 通道推过一次后的
          分叉后遗症：commit sha 不同但 tree 一致）——以远端为 base
          重建，最终用「新 tree == 本地 tree」做等价硬校验。
    其余形态（本地多条领先 / 内容真分叉）一律拒绝，不 force。

校验：
  - 重建 commit 的 tree sha 必须与本地 commit 的 tree sha 一致（内容/model
    完全对齐才落 ref）；不一致只撤回 blob/tree（不碰 ref），报错退出。

★ blob 内容一律用 `git cat-file blob <sha>` 从**提交对象**里取，不读磁盘：
  磁盘上可能是 CRLF（Windows 工作区 + .gitattributes 的 text=auto），而
  提交里存的是 LF —— 直接 open(path,"rb") 上传会得到一个与本地 commit
  不同的 blob，tree 必然对不上。这个坑的真实症状是
  「tree 不一致：重建=eda2423... 本地=18c4c1e...」，而 `git status` 干净、
  `git hash-object` 又报 SAME（它默认过 eol 过滤器，和 API 走的原始字节
  不是一回事）。
"""
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "liehuu/oka-wifi-tweak"
API = "https://api.github.com/repos/%s" % REPO


def sh(*args):
    out = subprocess.run(args, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def get_token():
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    sys.exit("凭据管理器里没有 github.com 的 token")


def api(method, path, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.exit("API %s %s -> %s: %s" % (method, path, e.code, detail[:400]))


def main():
    token = get_token()
    local = sh("git", "rev-parse", "HEAD")
    parent = sh("git", "rev-parse", "HEAD^")
    remote = api("GET", "/git/refs/heads/main", token)["object"]["sha"]
    remote_tree = api("GET", "/git/commits/%s" % remote, token)["tree"]["sha"]
    local_tree = sh("git", "rev-parse", "%s^{tree}" % local)

    if parent == remote:
        base = remote
        print("  形态：单 commit 领先（parent == 远端 main）")
    elif sh("git", "rev-parse", "%s^{tree}" % parent) == remote_tree:
        # API 通道推过一次后：本地 commit 与远端 commit 内容同树、sha 不同
        # （committer/author date 序列化差异）。以远端 main 为 base 重建，
        # 最终用「新 tree == 本地 tree」做等价硬校验，不靠口头保证。
        base = remote
        print("  形态：本地 parent 与远端 main 同树（等价）→ 以远端为 base 重建")
    else:
        sys.exit("非 FF：本地 parent=%s 远端 main=%s —— 本脚本拒绝 force 形态"
                 % (parent, remote))
    base_tree = remote_tree

    # 本地这一个 commit 改了哪些文件（相对 parent）
    diff_names = sh("git", "diff", "--name-only", parent, local).splitlines()
    if not diff_names:
        sys.exit("本地无改动文件")
    entries = {e["path"]: e for e in
               api("GET", "/git/trees/%s?recursive=1" % base_tree, token)["tree"]
               if e["type"] == "blob"}
    new_entries = []
    for path in diff_names:
        # 从提交对象取字节（LF 规范化后的真实内容），不读工作区磁盘文件
        raw = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (local, path)],
                             capture_output=True, check=True).stdout
        blob = api("POST", "/git/blobs", token,
                   {"content": base64.b64encode(raw).decode(),
                    "encoding": "base64"})
        e = entries.get(path)
        mode = e["mode"] if e else "100644"
        if e and e.get("sha") == blob["sha"]:
            continue  # 未变
        new_entries.append({"path": path, "mode": mode,
                            "type": "blob", "sha": blob["sha"]})
        print("  blob %s (%d bytes, mode %s)" % (path, len(raw), mode))
    if not new_entries:
        sys.exit("内容无变化（远端已是最新）")

    tree = api("POST", "/git/trees", token,
               {"base_tree": base_tree, "tree": new_entries})
    if tree["sha"] != local_tree:
        sys.exit("tree 不一致：重建=%s 本地=%s —— 不落 ref" %
                 (tree["sha"], local_tree))
    print("  tree 一致：%s" % tree["sha"])

    fmt = "%an%n%ae%n%aI%n%cn%n%ce%n%cI%n%B"
    author_n, author_e, author_d, comm_n, comm_e, comm_d, msg = \
        sh("git", "log", "-1", "--format=" + fmt, local).split("\n", 6)
    commit = api("POST", "/git/commits", token, {
        "message": msg,
        "tree": tree["sha"],
        "parents": [remote],
        "author": {"name": author_n, "email": author_e, "date": author_d},
        "committer": {"name": comm_n, "email": comm_e, "date": comm_d},
    })
    print("  commit 一致：%s" % commit["sha"])
    if commit["tree"]["sha"] != local_tree:
        sys.exit("commit.tree 不一致：%s != %s" % (commit["tree"]["sha"], local_tree))

    ref = api("PATCH", "/git/refs/heads/main", token, {"sha": commit["sha"]})
    print("  refs/heads/main -> %s（force=false）" % ref["object"]["sha"])
    print("PUSH_VIA_API_OK local=%s remote=%s" % (local, ref["object"]["sha"]))


if __name__ == "__main__":
    main()