/*
 * OkaWiFi v1.1.2 — GCDWebUploader 网页端增强层
 *
 * 注入方式：由 Tweak.xm hook +[GCDWebServerDataResponse responseWithHTMLTemplate:variables:]
 *          把本文件 + oka-wifi.css 追加到 epilogue 变量里，随首页 HTML 一起下发。
 *
 * 布局原则（v1.1.1 按用户反馈改）：
 *   页面默认状态与原版几乎一致，不新增任何占空间的可见区块。
 *   · 拖放在整个页面都可用，所以**没有单独的拖放框**；只在拖入时显示全屏提示层。
 *   · 上传任务面板默认隐藏，只在有任务时出现；结果条复用原页面的 #alerts。
 *   · 全选/取消选择放进原 .btn-toolbar，与原按钮同一行。
 *   · 移动**复用原版入口**：点行末的 .button-move，弹出的仍是原版 #move-modal，
 *     只是把「只能手填地址」替换成目录浏览 + 批量选中 + 结果反馈。
 *     不再有独立的「移动选中项」按钮和独立弹窗。
 *
 * v1.1.2 两处补齐（按真机反馈）：
 *   · 列表排序：原版按服务端返回顺序直接渲染，目录和文件穿插；在 GET list
 *     的响应源头原地 sort，文件夹置顶、组内按名称自然序。
 *   · 顶部紧凑：header 模板变量那行大标题 + 30px 工具栏上边距把文件列表顶得太靠下，
 *     用 body.oka-compact 作用域收紧，标题保留为一行小字。
 *
 * 设计约束：
 *   1. 原生 "Upload Files" 按钮 / #fileupload 的点击选择流程完全不动；
 *   2. 原生 #listing / _reload / #create-modal 完全不动；
 *   3. 新增功能只通过「追加 DOM + 事件委托 / 捕获阶段拦截」实现。
 *
 * 服务端接口（由 GCDWebServer 二进制反汇编确认）：
 *   GET  list?path=<dir>                  -> JSON 数组；目录的 path 以 "/" 结尾
 *   POST upload {path:<已存在目录>, files[]:...} -> 目录不存在或不是目录会失败
 *   POST create {path:<新目录完整路径>}   -> createDirectoryAtPath:withIntermediateDirectories:NO
 *                                            => 父目录必须已存在，且目标不能已存在
 *   POST move   {oldPath, newPath}        -> newPath 是完整目标路径；已存在会失败
 *   路径一律经 GCDWebServerNormalizePath 规范化。
 */
(function () {
  'use strict';

  if (window.__okaWiFiLoaded) { return; }
  window.__okaWiFiLoaded = true;

  var $ = window.jQuery;
  if (!$) { return; }

  // ---------------------------------------------------------------------
  // 0a. 列表排序：文件夹置顶，组内按名称自然序
  //
  // 原版 _reload() 拿到 GET list 的数组后直接按索引 append 行，不做任何排序；
  // 服务端给的是文件系统顺序，于是目录和文件穿插着显示。这里在**数据源头**
  // 修：给 jqXHR 注册第一个 done 回调，对数组原地 sort，原版随后注册的
  // .done(function(data){...}) 拿到的是同一个数组对象，渲染出来就是有序的。
  // 不用 DOM 重排，是因为重排会二次触发 MutationObserver，还会看到先乱后齐的闪烁。
  //
  // ★ 包装必须放在 IIFE 顶层、不能放进 $(document).ready()：
  //   原版 index.js 的 ready 回调里就会调 _reload("/")。本注入脚本位于
  //   epilogue 占位符（第 92 行），在 index.js（第 53 行）之后加载、但一定早于
  //   DOMContentLoaded，所以在此处包装能连首屏列表一起覆盖。
  //
  // 判据用「结构特征」而不是 URL 白名单：只处理每一项都形如
  // {path:string, name:string, size:number|null} 的数组。upload/create/move
  // 返回的是对象，不会误伤；将来服务端换个 list 地址也不会漏。
  // URL 只用来缩小注册范围（减少无关回调），不作为唯一判据。
  // ---------------------------------------------------------------------
  function isListingArray(data) {
    if (!Array.isArray(data) || data.length === 0) { return false; }
    for (var i = 0; i < data.length; ++i) {
      var o = data[i];
      if (!o || typeof o.path !== 'string' || typeof o.name !== 'string') {
        return false;
      }
    }
    return true;
  }

  function isDirEntry(o) {
    var p = o.path || '';
    // 目录项 path 以 "/" 结尾且 size 为 null；两者互为佐证，取并集更抗服务端差异
    return p.charAt(p.length - 1) === '/' || o.size == null;
  }

  function compareEntry(a, b) {
    var da = isDirEntry(a), db = isDirEntry(b);
    if (da !== db) { return da ? -1 : 1; }        // 文件夹永远排最上面
    var an = String(a.name || ''), bn = String(b.name || '');
    var c = 0;
    try {
      // numeric:true 让 "2.log" 排在 "10.log" 前面；sensitivity:'base' 忽略大小写
      c = an.localeCompare(bn, undefined, { numeric: true, sensitivity: 'base' });
    } catch (e) {
      var al = an.toLowerCase(), bl = bn.toLowerCase();
      c = al < bl ? -1 : (al > bl ? 1 : 0);
    }
    if (c !== 0) { return c; }
    // 同名但大小写不同时给个稳定次序，避免不同浏览器 sort 结果抖动
    return an < bn ? -1 : (an > bn ? 1 : 0);
  }

  function sortListingInPlace(data) {
    if (!isListingArray(data)) { return false; }
    data.sort(compareEntry);
    return true;
  }

  function isListGet(args) {
    var a0 = args[0], a1 = args[1];
    var opts = (typeof a0 === 'string') ? (a1 || {}) : (a0 || {});
    var url = (typeof a0 === 'string') ? a0 : (opts.url || '');
    if (String(opts.type || 'GET').toUpperCase() !== 'GET') { return false; }
    var clean = String(url).split('?')[0].split('#')[0];
    return /(^|\/)list$/i.test(clean);
  }

  var origAjax = $.ajax;
  $.ajax = function () {
    var jq = origAjax.apply(this, arguments);
    if (isListGet(arguments) && jq && typeof jq.done === 'function') {
      // 先于原版的 .done 注册，jQuery Deferred 按注册顺序执行
      jq.done(function (data) { sortListingInPlace(data); });
    }
    return jq;
  };

  // ---------------------------------------------------------------------
  // 0. 工具
  // ---------------------------------------------------------------------
  function curPath() {
    return (typeof window._path === 'string' && window._path) ? window._path : '/';
  }

  function reload() {
    if (typeof window._reload === 'function') { window._reload(curPath()); }
  }

  function withSlash(p) {
    if (!p) { return '/'; }
    if (p.charAt(0) !== '/') { p = '/' + p; }
    if (p.charAt(p.length - 1) !== '/') { p += '/'; }
    return p;
  }

  function noSlash(p) {
    if (!p) { return ''; }
    while (p.length > 1 && p.charAt(p.length - 1) === '/') { p = p.slice(0, -1); }
    return p;
  }

  function joinPath(dir, name) { return withSlash(dir) + name; }

  function baseName(p) {
    var s = noSlash(p);
    var i = s.lastIndexOf('/');
    return i >= 0 ? s.slice(i + 1) : s;
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtSize(b) {
    if (b >= 1000000000) { return (b / 1000000000).toFixed(2) + ' GB'; }
    if (b >= 1000000) { return (b / 1000000).toFixed(2) + ' MB'; }
    return (b / 1000).toFixed(2) + ' KB';
  }

  // ---------------------------------------------------------------------
  // 1. DOM 片段：都是默认隐藏 / 塞进原容器，不占额外版面
  // ---------------------------------------------------------------------
  // 上传任务面板：插在原 .uploading 面板之后。
  // 整块默认 display:none；有任务时只显示**一条折叠的头部**，明细区要点
  // 「展开」才出现 —— 用户反馈任务明细框太占地方，看的时候再点开即可。
  // 头部仍带汇总与三个操作按钮，折叠状态下进度/失败反馈照样看得到、也能重试。
  var HTML_TASKS = [
    '<div id="oka-tasks-wrap" class="oka-tasks-wrap panel panel-default oka-tasks-collapsed" style="display:none">',
    '  <div class="panel-heading oka-tasks-head">',
    '    <button type="button" class="btn btn-xs btn-default oka-tasks-toggle" id="oka-tasks-toggle" aria-expanded="false">&#9656; 展开</button>',
    '    <strong id="oka-tasks-title">上传任务</strong>',
    '    <span id="oka-tasks-summary" class="oka-hint"></span>',
    '    <button type="button" class="btn btn-xs btn-default pull-right" id="oka-tasks-clear">清除记录</button>',
    '    <button type="button" class="btn btn-xs btn-default pull-right" id="oka-tasks-retry">重试失败项</button>',
    '    <button type="button" class="btn btn-xs btn-warning pull-right" id="oka-tasks-stop">停止剩余</button>',
    '  </div>',
    '  <div id="oka-tasks" class="oka-tasks" style="display:none"></div>',
    '</div>'
  ].join('\n');

  // 全屏拖放提示：拖入时才显示，pointer-events:none 不抢 drop 事件
  var HTML_OVERLAY = [
    '<div id="oka-drag-overlay" class="oka-drag-overlay" style="display:none">',
    '  <div class="oka-drag-box">',
    '    <div class="oka-drag-box-icon">&#8681;</div>',
    '    <div class="oka-drag-box-title">松开即可上传</div>',
    '    <div class="oka-drag-box-sub">目标目录 <code id="oka-drag-path">/</code> &middot; 支持文件、多文件与整个文件夹</div>',
    '  </div>',
    '</div>'
  ].join('\n');

  // 列表工具：追加到原 .btn-toolbar，与原按钮同一行
  var HTML_LIST_TOOLS = [
    '<span class="oka-list-tools">',
    '  <button type="button" class="btn btn-default btn-sm" id="oka-select-all">全选</button>',
    '  <button type="button" class="btn btn-default btn-sm" id="oka-select-none">取消选择</button>',
    '  <span class="oka-hint">已选 <span id="oka-sel-count" class="badge">0</span> 项 &middot; 点行末移动按钮可批量移动</span>',
    '</span>'
  ].join('\n');

  // 追加到原版 #move-modal .modal-body：目录浏览 + 本次移动项 + 结果
  // 目标路径仍用原版自带的 #move-input（可手填，也可由目录浏览自动填入）
  var HTML_MOVE_ENHANCE = [
    '<div class="oka-move-section">',
    '  <label>本次移动 <span id="oka-move-count">1</span> 项</label>',
    '  <div id="oka-move-items" class="oka-move-items"></div>',
    '  <div class="oka-hint">未勾选时只移动你点击的那一项；勾选多个后点移动按钮即批量移动</div>',
    '</div>',
    '<div class="oka-move-section">',
    '  <label>目录浏览（点目录自动填入上面的目标路径）</label>',
    '  <div id="oka-move-crumb" class="oka-move-crumb"></div>',
    '  <div id="oka-move-browser" class="oka-move-browser"></div>',
    '</div>',
    '<div id="oka-move-result"></div>'
  ].join('\n');

  // ---------------------------------------------------------------------
  // 2. 任务面板与结果条
  // ---------------------------------------------------------------------
  var tasks = [];
  var running = false;
  var cancelled = false;
  var tasksExpanded = false;   // 明细区默认折叠，只有用户主动点「展开」才显示
  var batchBase = '/';   // 本批次目标根目录：用户中途切换目录后重试仍回原处

  function tasksVisible(v) { $('#oka-tasks-wrap').toggle(!!v); }

  function setTasksExpanded(v) {
    tasksExpanded = !!v;
    $('#oka-tasks').toggle(tasksExpanded);
    $('#oka-tasks-wrap').toggleClass('oka-tasks-collapsed', !tasksExpanded);
    $('#oka-tasks-toggle')
      .html(tasksExpanded ? '&#9662; 收起' : '&#9656; 展开')
      .attr('aria-expanded', tasksExpanded ? 'true' : 'false');
    if (tasksExpanded) {
      var box = $('#oka-tasks');
      if (box.length) { box.scrollTop(box[0].scrollHeight); }
    }
  }

  function renderTasks() {
    var done = 0, failed = 0, active = 0, waiting = 0;
    var rows = [];
    for (var i = 0; i < tasks.length; ++i) {
      var t = tasks[i];
      if (t.state === 'ok') { done++; }
      else if (t.state === 'fail') { failed++; }
      else if (t.state === 'run') { active++; }
      else { waiting++; }
      var badge;
      if (t.state === 'ok') { badge = '<span class="label label-success">完成</span>'; }
      else if (t.state === 'fail') { badge = '<span class="label label-danger">失败</span>'; }
      else if (t.state === 'run') { badge = '<span class="label label-info">进行中</span>'; }
      else if (t.state === 'skip') { badge = '<span class="label label-default">跳过</span>'; }
      else { badge = '<span class="label label-default">等待</span>'; }
      var bar = (t.kind === 'file' && t.state === 'run')
        ? '<div class="progress oka-progress"><div class="progress-bar" style="width:' + (t.pct || 0) + '%"></div></div>' : '';
      var detail = t.error ? '<div class="oka-task-error">' + esc(t.error) + '</div>' : '';
      rows.push(
        '<div class="oka-task oka-task-' + t.state + '">' +
          '<div class="oka-task-line"><span class="oka-task-name">' + esc(t.label) + '</span>' +
          '<span class="oka-task-state">' + badge + '</span>' +
          (t.kind === 'file' ? '<span class="oka-task-size">' + fmtSize(t.file.size) + '</span>' : '') +
          (t.state === 'run' ? '<span class="oka-task-pct">' + (t.pct || 0) + '%</span>' : '') +
          '</div>' + bar + detail +
        '</div>'
      );
    }
    $('#oka-tasks').html(rows.join(''));
    var wrap = $('#oka-tasks');
    if (wrap.length) { wrap.scrollTop(wrap[0].scrollHeight); }
    $('#oka-tasks-summary').text(
      '共 ' + tasks.length + ' 项 · 完成 ' + done + ' · 失败 ' + failed +
      ' · 进行中 ' + active + ' · 等待 ' + waiting
    );
    $('#oka-tasks-title').text(running ? '上传任务（进行中）' : '上传任务');
    $('#oka-tasks-retry').prop('disabled', running || failed === 0);
    $('#oka-tasks-stop').prop('disabled', !running);
  }

  // 复用原页面的 #alerts，不新增常驻容器；同时只保留一条本插件的结果
  function showResult(level, html) {
    var box = $('#alerts');
    if (!box.length) { return; }
    box.find('.oka-result-alert').remove();
    box.prepend(
      '<div class="alert alert-' + level + ' alert-dismissable oka-result-alert">' +
      '<button type="button" class="close" data-dismiss="alert">&times;</button>' + html + '</div>'
    );
  }

  function clearResult() { $('#alerts .oka-result-alert').remove(); }

  // ---------------------------------------------------------------------
  // 3. 拖放接管（全页面都是放置区，没有单独的框）
  //
  // 原页面把 dropZone 设成 $(document)，且 add 回调把 formData.path 写死为 _path。
  // 文件夹上传需要给不同子目录指定 path，因此在 **捕获阶段** 接管 drop：
  //   preventDefault + stopPropagation 让浏览器不打开文件，
  //   同时阻止事件冒泡到 jQuery fileupload 在 document 上的 handler，避免双份上传。
  // 点击 "Upload Files" 选择文件走 change 事件，完全不受影响。
  // ---------------------------------------------------------------------
  var dragDepth = 0;

  function isFileDrag(e) {
    var dt = e.dataTransfer;
    if (!dt) { return false; }
    var types = dt.types || [];
    for (var i = 0; i < types.length; ++i) {
      if (String(types[i]).toLowerCase() === 'files') { return true; }
    }
    return false;
  }

  function onDragEnter(e) {
    if (!isFileDrag(e)) { return; }
    e.preventDefault();
    e.stopPropagation();
    dragDepth++;
    $('#oka-drag-path').text(curPath());
    $('#oka-drag-overlay').show();
  }

  function onDragOver(e) {
    if (!isFileDrag(e)) { return; }
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) { e.dataTransfer.dropEffect = 'copy'; }
  }

  function onDragLeave(e) {
    if (!isFileDrag(e)) { return; }
    e.preventDefault();
    e.stopPropagation();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) { $('#oka-drag-overlay').hide(); }
  }

  function hideDragUI() { dragDepth = 0; $('#oka-drag-overlay').hide(); }

  function onDrop(e) {
    if (!isFileDrag(e)) { return; }
    e.preventDefault();
    e.stopPropagation();
    hideDragUI();
    if (running) {
      showResult('warning', '<strong>已有上传任务在进行</strong> 请等当前批次结束后再拖入。');
      return;
    }
    try {
      collectDropped(e.dataTransfer).then(startUpload).catch(function (err) {
        showResult('danger', '<strong>读取拖入内容失败</strong> ' + esc(err && err.message ? err.message : err));
      });
    } catch (err2) {
      showResult('danger', '<strong>拖放处理异常</strong> ' + esc(err2 && err2.message ? err2.message : err2));
    }
  }

  // ---------------------------------------------------------------------
  // 4. 递归展开 DataTransferItem（文件夹）
  // ---------------------------------------------------------------------
  function collectDropped(dt) {
    var items = dt.items || [];
    var entries = [];
    for (var i = 0; i < items.length; ++i) {
      var it = items[i];
      if (it.kind !== 'file') { continue; }
      var en = (it.webkitGetAsEntry || it.getAsEntry || function () { return null; }).call(it);
      if (en) { entries.push(en); }
    }

    // 浏览器不提供 entry API 时降级：只处理 dataTransfer.files
    if (!entries.length) {
      var plain = [];
      for (var j = 0; j < (dt.files || []).length; ++j) {
        plain.push({ file: dt.files[j], relDir: '' });
      }
      return Promise.resolve({ dirs: [], files: plain });
    }

    var out = { dirs: [], files: [] };

    function readAll(reader, acc) {
      return new Promise(function (resolve, reject) {
        reader.readEntries(function (batch) {
          if (!batch || batch.length === 0) { resolve(acc); return; }
          for (var k = 0; k < batch.length; ++k) { acc.push(batch[k]); }
          // readEntries 单次最多 100 条，必须循环读到空为止
          readAll(reader, acc).then(resolve, reject);
        }, reject);
      });
    }

    function walk(entry, base) {
      if (entry.isFile) {
        return new Promise(function (resolve, reject) {
          entry.file(function (f) {
            out.files.push({ file: f, relDir: base });
            resolve();
          }, reject);
        });
      }
      if (!entry.isDirectory) { return Promise.resolve(); }

      var rel = base + entry.name + '/';
      out.dirs.push(rel);
      return readAll(entry.createReader(), []).then(function (children) {
        var chain = Promise.resolve();
        for (var n = 0; n < children.length; ++n) {
          (function (ch) {
            chain = chain.then(function () { return walk(ch, rel); });
          })(children[n]);
        }
        return chain;
      });
    }

    var chain = Promise.resolve();
    for (var m = 0; m < entries.length; ++m) {
      (function (en) {
        chain = chain.then(function () { return walk(en, ''); });
      })(entries[m]);
    }
    return chain.then(function () {
      out.dirs.sort(function (a, b) {
        var da = a.split('/').length - 1, db = b.split('/').length - 1;
        return da - db || a.localeCompare(b);
      });
      return out;
    });
  }

  // ---------------------------------------------------------------------
  // 5. 上传队列：先逐级 create，再串行 upload
  // ---------------------------------------------------------------------
  function ajaxForm(url, data) {
    return new Promise(function (resolve, reject) {
      $.ajax({ url: url, type: 'POST', data: data, dataType: 'json' })
        .done(function (r) { resolve(r); })
        .fail(function (xhr, status, err) {
          reject(new Error((err || status || 'error') + ' (HTTP ' + xhr.status + ')'));
        });
    });
  }

  // create 用 withIntermediateDirectories:NO，目标已存在会直接失败。
  // 重复拖入同名文件夹时反查 list，确认同名目录确实存在才当「已就绪」。
  function dirAlreadyExists(absNoSlash) {
    var cut = absNoSlash.lastIndexOf('/');
    var parent = cut > 0 ? absNoSlash.slice(0, cut + 1) : '/';
    return new Promise(function (resolve) {
      $.ajax({ url: 'list', type: 'GET', data: { path: parent }, dataType: 'json' })
        .done(function (data) {
          var arr = data || [], found = false;
          for (var k = 0; k < arr.length; ++k) {
            var o = arr[k];
            if (o && o.path && o.size == null && noSlash(o.path) === absNoSlash) { found = true; break; }
          }
          resolve(found);
        })
        .fail(function () { resolve(false); });
    });
  }

  function uploadOne(task, targetPath) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData();
      fd.append('path', targetPath);
      fd.append('files[]', task.file, task.file.name);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', 'upload', true);
      xhr.upload.onprogress = function (ev) {
        if (ev.lengthComputable) {
          task.pct = Math.min(99, Math.round(ev.loaded / ev.total * 100));
          renderTasks();
        }
      };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) {
          task.pct = 100;
          resolve();
        } else {
          reject(new Error('HTTP ' + xhr.status + ' ' + (xhr.statusText || '')));
        }
      };
      xhr.onerror = function () { reject(new Error('网络错误')); };
      xhr.onabort = function () { reject(new Error('已取消')); };
      task.xhr = xhr;
      xhr.send(fd);
    });
  }

  function buildBatch(collected) {
    var list = [];
    var base = curPath();
    for (var i = 0; i < collected.dirs.length; ++i) {
      list.push({
        kind: 'dir', label: '目录 ' + collected.dirs[i],
        relDir: collected.dirs[i], state: 'wait', pct: 0, error: '', file: null, xhr: null
      });
    }
    for (var j = 0; j < collected.files.length; ++j) {
      var f = collected.files[j];
      list.push({
        kind: 'file', label: f.relDir + f.file.name,
        relDir: f.relDir, file: f.file, state: 'wait', pct: 0, error: '', xhr: null
      });
    }
    return { list: list, base: base };
  }

  function startUpload(collected) {
    if (!collected.files.length && !collected.dirs.length) {
      showResult('warning', '<strong>没有可上传的内容</strong> 拖入的内容为空或浏览器不允许读取。');
      return;
    }
    var batch = buildBatch(collected);
    tasks = batch.list;
    batchBase = batch.base;
    cancelled = false;
    running = true;
    tasksVisible(true);
    setTasksExpanded(false);   // 每一批开始都回到折叠，需要明细再点开
    showResult('info', '<strong>开始上传</strong> 目录 ' + collected.dirs.length +
      ' 个 · 文件 ' + collected.files.length + ' 个 → ' + esc(batch.base));
    renderTasks();
    runQueue(batch.base);
  }

  function runQueue(base) {
    var i = 0;
    var okCount = 0, failCount = 0;
    var failedDirs = {};

    function next() {
      if (cancelled) { return finish(true); }
      if (i >= tasks.length) { return finish(false); }
      var t = tasks[i++];
      if (t.state === 'ok') { okCount++; return next(); }

      if (t.kind === 'dir') {
        var relNoSlash = noSlash(joinPath(base, t.relDir));
        var parent = relNoSlash.slice(0, relNoSlash.lastIndexOf('/'));
        if (failedDirs[parent]) {
          t.state = 'skip'; t.error = '上级目录未创建成功';
          failedDirs[relNoSlash] = true;
          renderTasks(); return next();
        }
        t.state = 'run'; renderTasks();
        return ajaxForm('create', { path: relNoSlash }).then(function () {
          t.state = 'ok'; okCount++; renderTasks(); next();
        }, function (err) {
          return dirAlreadyExists(relNoSlash).then(function (exists) {
            if (exists) {
              t.state = 'ok'; t.error = ''; okCount++;
            } else {
              t.state = 'fail'; t.error = err.message + '（且目录不存在）'; failCount++;
              failedDirs[relNoSlash] = true;
            }
            renderTasks(); next();
          });
        });
      }

      var target = withSlash(joinPath(base, t.relDir));
      var dirKey = noSlash(target);
      if (failedDirs[dirKey]) {
        t.state = 'skip'; t.error = '目标目录未创建成功';
        renderTasks(); return next();
      }
      t.state = 'run'; t.pct = 0; renderTasks();
      return uploadOne(t, target).then(function () {
        t.state = 'ok'; okCount++; renderTasks(); next();
      }, function (err) {
        t.state = 'fail'; t.error = err.message; failCount++; renderTasks(); next();
      });
    }

    function finish(wasCancelled) {
      running = false;
      renderTasks();
      reload();
      if (wasCancelled) {
        showResult('warning', '<strong>上传已取消</strong> 已完成的部分已保留在手机上。');
      } else if (failCount === 0) {
        showResult('success', '<strong>上传完成</strong> 全部 ' + okCount + ' 项成功 → ' + esc(base));
      } else {
        showResult('danger', '<strong>上传完成，但有失败项</strong> 成功 ' + okCount +
          ' 项 · 失败 / 跳过 ' + failCount + ' 项。可在任务列表里点「重试失败项」。');
      }
    }

    next();
  }

  function retryFailed() {
    if (running) { return; }
    var has = false;
    for (var i = 0; i < tasks.length; ++i) {
      if (tasks[i].state === 'fail' || tasks[i].state === 'skip') {
        tasks[i].state = 'wait'; tasks[i].error = ''; tasks[i].pct = 0; has = true;
      }
    }
    if (!has) { return; }
    cancelled = false; running = true;
    renderTasks();
    runQueue(batchBase);
  }

  // ---------------------------------------------------------------------
  // 6. 列表多选：MutationObserver 只往新行前面追加一个复选框 td
  // ---------------------------------------------------------------------
  var selected = {};   // path -> {path,name,isDir}

  function injectChecks() {
    var listing = document.getElementById('listing');
    if (!listing) { return; }
    var rows = listing.querySelectorAll('tr.row-file');
    for (var i = 0; i < rows.length; ++i) {
      var tr = rows[i];
      if (tr.querySelector('.oka-check')) { continue; }
      var td = document.createElement('td');
      td.className = 'oka-check';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'oka-checkbox';
      td.appendChild(cb);
      tr.insertBefore(td, tr.firstChild);
    }
  }

  function syncSelectionUI() {
    var keys = Object.keys(selected);
    var map = {};
    for (var i = 0; i < keys.length; ++i) { map[keys[i]] = true; }
    $('#listing').find('tr.row-file').each(function () {
      var d = $(this).data() || {};
      var on = !!(d.path && map[d.path]);
      $(this).find('.oka-checkbox').prop('checked', on);
      $(this).toggleClass('oka-selected', on);
    });
    $('#oka-sel-count').text(keys.length);
  }

  function bindSelection() {
    injectChecks();
    if (window.MutationObserver) {
      var listing = document.getElementById('listing');
      if (listing) {
        new MutationObserver(function () {
          injectChecks();
          syncSelectionUI();
        }).observe(listing, { childList: true });
      }
    } else {
      setInterval(function () { injectChecks(); syncSelectionUI(); }, 600);
    }

    $(document).on('click', '#listing tr.row-file', function (e) {
      if ($(e.target).closest('button, a, input, .edit').length) { return; }
      var d = $(this).data() || {};
      if (!d.path) { return; }
      if (selected[d.path]) { delete selected[d.path]; }
      else { selected[d.path] = { path: d.path, name: d.name || baseName(d.path), isDir: (d.size == null) }; }
      syncSelectionUI();
      e.preventDefault();
    });

    $(document).on('change', '#listing .oka-checkbox', function (e) {
      var d = $(this).closest('tr.row-file').data() || {};
      if (!d.path) { return; }
      if (this.checked) { selected[d.path] = { path: d.path, name: d.name || baseName(d.path), isDir: (d.size == null) }; }
      else { delete selected[d.path]; }
      syncSelectionUI();
      e.stopPropagation();
    });

    $('#oka-select-all').on('click', function () {
      $('#listing').find('tr.row-file').each(function () {
        var d = $(this).data() || {};
        if (d.path) { selected[d.path] = { path: d.path, name: d.name || baseName(d.path), isDir: (d.size == null) }; }
      });
      syncSelectionUI();
    });
    $('#oka-select-none').on('click', function () { selected = {}; syncSelectionUI(); });
  }

  // ---------------------------------------------------------------------
  // 7. 移动：复用原版 .button-move 入口与原版 #move-modal，
  //    把「只能手填地址」替换成目录浏览 + 批量 + 结果反馈。
  // ---------------------------------------------------------------------
  var pendingMove = [];
  var browseDir = '/';

  function renderMoveItems() {
    $('#oka-move-count').text(pendingMove.length);
    if (!pendingMove.length) {
      $('#oka-move-items').html('<span class="oka-hint">未选中任何项目</span>');
      return;
    }
    var rows = [];
    for (var i = 0; i < pendingMove.length; ++i) {
      var it = pendingMove[i];
      rows.push('<span class="oka-chip">' + (it.isDir ? '&#128193; ' : '&#128196; ') + esc(it.name) + '</span>');
    }
    $('#oka-move-items').html(rows.join(''));
  }

  // 目标路径写回原版自带的 #move-input（它本来就是给用户手填的）
  function loadBrowse(dir) {
    browseDir = withSlash(dir);
    $('#move-input').val(browseDir);
    var parts = browseDir.split('/').filter(Boolean);
    var crumbs = ['<a href="#" data-dir="/">根目录</a>'];
    var acc = '/';
    for (var i = 0; i < parts.length; ++i) {
      acc += parts[i] + '/';
      crumbs.push('<span>/</span><a href="#" data-dir="' + esc(acc) + '">' + esc(parts[i]) + '</a>');
    }
    $('#oka-move-crumb').html(crumbs.join(' '));
    $('#oka-move-browser').html('<span class="oka-hint">载入中…</span>');

    $.ajax({ url: 'list', type: 'GET', data: { path: browseDir }, dataType: 'json' })
      .done(function (data) {
        var arr = data || [], dirs = [];
        for (var k = 0; k < arr.length; ++k) {
          var o = arr[k];
          if (o && o.path && o.path.charAt(o.path.length - 1) === '/') { dirs.push(o); }
        }
        if (!dirs.length) { $('#oka-move-browser').html('<span class="oka-hint">此目录下没有子目录</span>'); return; }
        var html = [];
        for (var n = 0; n < dirs.length; ++n) {
          html.push('<a href="#" class="oka-dir" data-dir="' + esc(dirs[n].path) + '">&#128193; ' + esc(dirs[n].name) + '</a>');
        }
        $('#oka-move-browser').html(html.join(''));
      })
      .fail(function (xhr, status, err) {
        $('#oka-move-browser').html('<span class="text-danger">读取目录失败：' + esc(err || status) + '</span>');
      });
  }

  // 捕获阶段拦下原版 .button-move 的 click：
  // 原版 handler 是每次 _reload 直接绑到 button 上的，冒泡阶段拦不住，
  // 必须在 document 的捕获阶段 stopPropagation，才能不弹出原版那个简陋逻辑。
  function onMoveButtonCapture(e) {
    var t = e.target;
    if (!t || !t.closest) { return; }
    var btn = t.closest('#listing .button-move');
    if (!btn) { return; }
    var tr = btn.closest('tr.row-file');
    if (!tr) { return; }
    e.preventDefault();
    e.stopPropagation();       // 原版 button handler 不再执行，不会重复弹窗/重复移动
    openEnhancedMove(tr);
  }

  function openEnhancedMove(tr) {
    var d = $(tr).data() || {};
    var keys = Object.keys(selected);
    if (keys.length) {
      pendingMove = keys.map(function (k) { return selected[k]; });
    } else if (d.path) {
      pendingMove = [{ path: d.path, name: d.name || baseName(d.path), isDir: (d.size == null) }];
    } else {
      pendingMove = [];
    }
    renderMoveItems();
    $('#oka-move-result').empty();
    // 每次打开都恢复可点击；doMove() 完成后会保持 disabled，
    // 防止用户看着结果再点一次造成重复 POST（第二次必然失败，像功能坏了）。
    $('#move-confirm').prop('disabled', !pendingMove.length);
    $('#move-modal').modal('show');
    loadBrowse(curPath());
  }

  // 接管原版 #move-confirm：原版只发一个 POST move 且立刻关窗，
  // 这里改成批量 + 逐项结果 + 保留弹窗反馈 + 结束后刷新列表。
  function doMove() {
    if (!pendingMove.length) { return; }
    var target = withSlash($('#move-input').val());
    $('#move-confirm').prop('disabled', true);
    var ok = 0, fail = 0, skipped = 0;
    var failures = [];
    var idx = 0;

    function next() {
      if (idx >= pendingMove.length) {
        // 保持 disabled：结果留在弹窗里给用户看，但不允许重复提交。
        // 下次点 .button-move 打开时由 openEnhancedMove() 恢复。
        var msg = '<strong>移动完成</strong> 成功 ' + ok + ' 项 · 失败 ' + fail +
                  ' 项 · 跳过 ' + skipped + ' 项 → ' + esc(target);
        if (failures.length) {
          msg += '<div class="oka-move-failures">' +
                 failures.map(function (s) { return esc(s); }).join('<br>') + '</div>';
        }
        var cls = fail ? 'danger' : 'success';
        $('#oka-move-result').html('<div class="alert alert-' + cls + '">' + msg + '</div>');
        showResult(cls, msg);
        selected = {};
        syncSelectionUI();
        // 不调用 renderMoveItems()：把「本次移动 N 项」保留为刚移动的批次，
        // 清零成「未选中任何项目」会和上面的成功汇总并排看着像失败。
        reload();
        return;
      }
      var it = pendingMove[idx++];
      var oldPath = noSlash(it.path);
      var newPath = noSlash(joinPath(target, it.name));
      $('#oka-move-result').html(
        '<div class="alert alert-info">正在移动 ' + idx + '/' + pendingMove.length +
        '：' + esc(it.name) + '…</div>');
      if (newPath === oldPath) { skipped++; return next(); }
      ajaxForm('move', { oldPath: oldPath, newPath: newPath }).then(function () {
        ok++; next();
      }, function (err) {
        fail++; failures.push(it.name + '：' + err.message); next();
      });
    }
    next();
  }

  // ---------------------------------------------------------------------
  // 8. 初始化（DOM ready 后，原 index.js 的 ready 已先执行完）
  // ---------------------------------------------------------------------
  $(document).ready(function () {
    try {
      // 紧凑顶部：body 上加作用域 class，CSS 只压 .container 的直接子元素。
      // 用 class 而不是直接改内联样式，关掉 EnableWebEnhance 时页面自然回到原版。
      $('body').addClass('oka-compact');

      // 任务面板插在原 .uploading 之后；默认隐藏，只有上传时才出现
      var up = $('.uploading').first();
      if (up.length) { up.after(HTML_TASKS); }
      else { $('.btn-toolbar').first().after(HTML_TASKS); }

      // 列表工具塞进原 .btn-toolbar，与原按钮同一行，不新增行
      $('.btn-toolbar').first().append(HTML_LIST_TOOLS);
      $('body').append(HTML_OVERLAY);

      // ---- 改造原版 #move-modal ----
      var mbody = $('#move-modal .modal-body');
      mbody.find('p').first().text('选择目标目录，或直接填写完整目标路径（移动后保持原文件名）：');
      mbody.append(HTML_MOVE_ENHANCE);
      $('#move-modal .modal-title').text('移动文件 / 文件夹');
      $('#move-confirm').text('移动');
      $('#move-confirm').off('click');        // 摘掉原版「单个 POST move + 立即关窗」
      $('#move-confirm').on('click', doMove);
      // 原版 #move-input 的 Enter -> $("#move-confirm").click() 保留复用

      // 捕获阶段接管原版行内移动按钮
      document.addEventListener('click', onMoveButtonCapture, true);

      // 捕获阶段接管拖放（全页面都是放置区）
      document.addEventListener('dragenter', onDragEnter, true);
      document.addEventListener('dragover', onDragOver, true);
      document.addEventListener('dragleave', onDragLeave, true);
      document.addEventListener('drop', onDrop, true);
      window.addEventListener('dragend', hideDragUI, true);

      $('#oka-tasks-toggle').on('click', function () {
        setTasksExpanded(!tasksExpanded);
      });
      setTasksExpanded(false);

      $('#oka-tasks-clear').on('click', function () {
        if (running) { return; }
        tasks = []; renderTasks(); tasksVisible(false); clearResult();
      });
      $('#oka-tasks-retry').on('click', retryFailed);
      $('#oka-tasks-stop').on('click', function () {
        if (!running) { return; }
        cancelled = true;
        for (var i = 0; i < tasks.length; ++i) {
          if (tasks[i].state === 'run' && tasks[i].xhr) {
            try { tasks[i].xhr.abort(); } catch (e) {}
          }
        }
      });

      $(document).on('click', '#oka-move-crumb a, #oka-move-browser a.oka-dir', function (e) {
        e.preventDefault();
        loadBrowse($(this).attr('data-dir'));
      });

      bindSelection();
      renderTasks();
    } catch (e) {
      try {
        $('.container').first().prepend(
          '<div class="alert alert-warning">OkaWiFi 增强层初始化失败，原页面功能不受影响：' +
          esc(e && e.message ? e.message : e) + '</div>'
        );
      } catch (ignored) {}
    }
  });
})();
