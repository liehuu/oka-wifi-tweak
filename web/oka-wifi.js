/*
 * OkaWiFi v1.1.0 — GCDWebUploader 网页端增强层
 *
 * 注入方式：由 Tweak.xm hook +[GCDWebServerDataResponse responseWithHTMLTemplate:variables:]
 *          把本文件 + oka-wifi.css 追加到模板的 epilogue 变量里，随首页 HTML 一起下发。
 *
 * 设计约束（用户明确要求）：
 *   1. 原生 "Upload Files" 按钮 / #fileupload 的点击选择流程完全不动；
 *   2. 原生 .button-move / #move-modal / _reload / #listing 渲染逻辑完全不动；
 *   3. 新增功能只通过"追加 DOM + 事件委托"实现，不替换、不重写任何原函数。
 *
 * 服务端接口（由 GCDWebServer 二进制反汇编确认，2026-11-07）：
 *   GET  list?path=<dir>                 -> JSON 数组；目录的 path 以 "/" 结尾
 *   POST upload  {path:<已存在目录>, files[]:...} -> 目录不存在或不是目录会失败
 *   POST create  {path:<新目录完整路径>}          -> createDirectoryAtPath:withIntermediateDirectories:NO
 *                                                    => 父目录必须已存在，且目标不能已存在
 *   POST move    {oldPath, newPath}                -> newPath 是完整目标路径；已存在会失败
 *   POST delete  {path}
 *   GET  download?path=
 *   路径一律经 GCDWebServerNormalizePath 规范化。
 *
 * 因此文件夹上传必须：目录按深度升序逐级 create，再逐个 upload 到对应子目录。
 */
(function () {
  'use strict';

  if (window.__okaWiFiLoaded) { return; }
  window.__okaWiFiLoaded = true;

  var $ = window.jQuery;
  if (!$) { return; }

  // ---------------------------------------------------------------------
  // 0. 工具
  // ---------------------------------------------------------------------
  function curPath() {
    // _path 是原页面全局变量，表示当前目录，始终以 "/" 结尾
    return (typeof window._path === 'string' && window._path) ? window._path : '/';
  }

  function reload() {
    // 直接调用原页面的 _reload，不替换、不包装
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
  // 1. DOM：拖放区 / 任务面板 / 批量工具条 / 移动弹窗 / 全屏提示层
  // ---------------------------------------------------------------------
  var HTML_PANEL = [
    '<div class="oka-panel panel panel-default" id="oka-panel">',
    '  <div class="panel-heading">',
    '    <strong>OkaWiFi 增强</strong>',
    '    <span class="oka-hint">拖拽上传 &middot; 批量移动</span>',
    '  </div>',
    '  <div class="panel-body">',
    '    <div id="oka-dropzone" class="oka-dropzone">',
    '      <div class="oka-dropzone-icon">&#8681;</div>',
    '      <div class="oka-dropzone-title">把文件或多个文件、整个文件夹拖到这里上传</div>',
    '      <div class="oka-dropzone-sub">当前目录：<code id="oka-current-path">/</code>&nbsp;&middot;&nbsp;文件夹会保留目录结构</div>',
    '    </div>',
    '    <div class="oka-batch-bar">',
    '      <button type="button" class="btn btn-sm btn-default" id="oka-select-all">全选</button>',
    '      <button type="button" class="btn btn-sm btn-default" id="oka-select-none">取消选择</button>',
    '      <button type="button" class="btn btn-sm btn-primary" id="oka-move-open" disabled>移动选中项 <span id="oka-sel-count" class="badge">0</span></button>',
    '      <span class="oka-hint">在上方文件列表中勾选，可一次移动多个文件 / 文件夹</span>',
    '    </div>',
    '    <div id="oka-tasks-wrap" class="oka-tasks-wrap" style="display:none">',
    '      <div class="oka-tasks-head">',
    '        <strong id="oka-tasks-title">上传任务</strong>',
    '        <span id="oka-tasks-summary" class="oka-hint"></span>',
    '        <button type="button" class="btn btn-xs btn-default pull-right" id="oka-tasks-clear">清除记录</button>',
    '        <button type="button" class="btn btn-xs btn-default pull-right" id="oka-tasks-retry">重试失败项</button>',
    '        <button type="button" class="btn btn-xs btn-warning pull-right" id="oka-tasks-stop">停止剩余</button>',
    '      </div>',
    '      <div id="oka-tasks" class="oka-tasks"></div>',
    '    </div>',
    '    <div id="oka-result"></div>',
    '  </div>',
    '</div>'
  ].join('\n');

  var HTML_OVERLAY = [
    '<div id="oka-drag-overlay" class="oka-drag-overlay" style="display:none">',
    '  <div class="oka-drag-box">',
    '    <div class="oka-drag-box-icon">&#8681;</div>',
    '    <div class="oka-drag-box-title">松开即可上传</div>',
    '    <div class="oka-drag-box-sub">目标目录 <code id="oka-drag-path">/</code> &middot; 支持文件、多文件与整个文件夹</div>',
    '  </div>',
    '</div>'
  ].join('\n');

  var HTML_MOVE_MODAL = [
    '<div class="modal fade" id="oka-move-modal" tabindex="-1">',
    '  <div class="modal-dialog modal-lg">',
    '    <div class="modal-content">',
    '      <div class="modal-header">',
    '        <button type="button" class="close" data-dismiss="modal">&times;</button>',
    '        <h4 class="modal-title">批量移动 / 移动到指定位置</h4>',
    '      </div>',
    '      <div class="modal-body">',
    '        <div class="oka-move-section">',
    '          <label>已选中 <span id="oka-move-count">0</span> 项</label>',
    '          <div id="oka-move-items" class="oka-move-items"></div>',
    '        </div>',
    '        <div class="oka-move-section">',
    '          <label for="oka-move-target">目标目录（移动后保持原文件名）</label>',
    '          <input type="text" class="form-control" id="oka-move-target" autocomplete="off" value="/">',
    '          <div class="oka-hint">也可以点下面的目录逐级浏览，选中后自动填入</div>',
    '        </div>',
    '        <div class="oka-move-section">',
    '          <label>目录浏览</label>',
    '          <div id="oka-move-crumb" class="oka-move-crumb"></div>',
    '          <div id="oka-move-browser" class="oka-move-browser"></div>',
    '        </div>',
    '        <div id="oka-move-result"></div>',
    '      </div>',
    '      <div class="modal-footer">',
    '        <button type="button" class="btn btn-default" data-dismiss="modal">关闭</button>',
    '        <button type="button" class="btn btn-primary" id="oka-move-confirm">移动选中项</button>',
    '      </div>',
    '    </div>',
    '  </div>',
    '</div>'
  ].join('\n');

  // ---------------------------------------------------------------------
  // 2. 任务面板
  // ---------------------------------------------------------------------
  var tasks = [];          // {kind:'dir'|'file', label, relDir, file, state, pct, error}
  var running = false;
  var cancelled = false;
  var batchBase = '/';   // 本批次的目标根目录：用户中途切换目录后，重试仍回原处

  function tasksVisible(v) { $('#oka-tasks-wrap').toggle(!!v); }

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

  function showResult(level, html) {
    $('#oka-result').html(
      '<div class="alert alert-' + level + ' alert-dismissable oka-result-alert">' +
      '<button type="button" class="close" data-dismiss="alert">&times;</button>' + html + '</div>'
    );
  }

  // ---------------------------------------------------------------------
  // 3. 拖放接管
  //
  // 原页面把 dropZone 设成 $(document)，且 add 回调把 formData.path 写死为 _path。
  // 文件夹上传需要给不同子目录指定 path，因此这里在 **捕获阶段** 接管 drop：
  //   preventDefault + stopPropagation 让浏览器不打开文件，
  //   同时阻止事件冒泡到 jQuery fileupload 在 document 上的 handler，避免双份上传。
  // 点击 "Upload Files" 选择文件走的是 change 事件，完全不受影响。
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
    $('#oka-dropzone').addClass('oka-dropzone-active');
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
    if (dragDepth === 0) {
      $('#oka-drag-overlay').hide();
      $('#oka-dropzone').removeClass('oka-dropzone-active');
    }
  }

  function hideDragUI() {
    dragDepth = 0;
    $('#oka-drag-overlay').hide();
    $('#oka-dropzone').removeClass('oka-dropzone-active');
  }

  function onDrop(e) {
    if (!isFileDrag(e)) { return; }
    e.preventDefault();
    e.stopPropagation();   // 关键：不让 jQuery fileupload 的 document drop handler 收到
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

    // 浏览器不提供 entry API 时降级：只处理 dataTransfer.files（单/多文件，无文件夹）
    if (!entries.length) {
      var plain = [];
      for (var j = 0; j < (dt.files || []).length; ++j) {
        var f = dt.files[j];
        plain.push({ file: f, relDir: '' });
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
      // 父目录必须先于子目录创建
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

  // create 用的是 withIntermediateDirectories:NO，目标已存在会直接失败。
  // 重复拖入同一个文件夹时，"已存在"不是错误，要当成目录已经就绪继续往下走。
  // 用 list 反查一次父目录，确认同名目录确实存在，避免把真实失败吞掉。
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

      // 目录：create 的父目录必须已存在，目标不能已存在（见文件头反汇编结论）
      if (t.kind === 'dir') {
        var relNoSlash = noSlash(joinPath(base, t.relDir));
        // 父目录在本批次里创建失败 -> 跳过，不产生一串必然失败的上传
        var parent = relNoSlash.slice(0, relNoSlash.lastIndexOf('/'));
        if (failedDirs[parent]) {
          t.state = 'skip'; t.error = '上级目录未创建成功';
          failedDirs[relNoSlash] = true;   // 让子目录 / 该目录下的文件一并跳过
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

      // 文件：目标目录必须已存在；若其所属目录建失败则跳过
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
      reload();   // 复用原页面刷新，不重写列表渲染
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
  //    不改动原 _reload 的渲染，也不覆盖任何原有事件绑定
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
      var p = d.path;
      var on = !!(p && map[p]);
      $(this).find('.oka-checkbox').prop('checked', on);
      $(this).toggleClass('oka-selected', on);
    });
    $('#oka-sel-count').text(keys.length);
    $('#oka-move-open').prop('disabled', keys.length === 0);
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

    // 事件委托：点击行内按钮时不要触发选中
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
      var tr = $(this).closest('tr.row-file');
      var d = tr.data() || {};
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
  // 7. 批量移动：目标目录浏览 + 逐个 POST move + 汇总反馈
  // ---------------------------------------------------------------------
  var browseDir = '/';

  function renderMoveItems() {
    var keys = Object.keys(selected);
    $('#oka-move-count').text(keys.length);
    if (!keys.length) { $('#oka-move-items').html('<span class="oka-hint">未选中任何项目</span>'); return; }
    var rows = [];
    for (var i = 0; i < keys.length; ++i) {
      var it = selected[keys[i]];
      rows.push('<span class="oka-chip">' + (it.isDir ? '&#128193; ' : '&#128196; ') + esc(it.name) + '</span>');
    }
    $('#oka-move-items').html(rows.join(''));
  }

  function loadBrowse(dir) {
    browseDir = withSlash(dir);
    $('#oka-move-target').val(browseDir);
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
        var arr = data || [];
        var dirs = [];
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

  function doMove() {
    var keys = Object.keys(selected);
    if (!keys.length) { return; }
    var target = withSlash($('#oka-move-target').val());
    $('#oka-move-confirm').prop('disabled', true);
    var ok = 0, fail = 0, skipped = 0;
    var failures = [];
    var idx = 0;

    function next() {
      if (idx >= keys.length) {
        $('#oka-move-confirm').prop('disabled', false);
        selected = {};
        syncSelectionUI();
        renderMoveItems();
        reload();
        var msg = '<strong>移动完成</strong> 成功 ' + ok + ' 项 · 失败 ' + fail + ' 项 · 跳过 ' + skipped + ' 项 → ' + esc(target);
        if (failures.length) {
          msg += '<div class="oka-move-failures">' + failures.map(function (s) { return esc(s); }).join('<br>') + '</div>';
        }
        $('#oka-move-result').html('<div class="alert alert-' + (fail ? 'danger' : 'success') + '">' + msg + '</div>');
        showResult(fail ? 'danger' : 'success', msg);
        return;
      }
      var it = selected[keys[idx++]];
      var oldPath = noSlash(it.path);
      var newPath = joinPath(target, it.name);
      $('#oka-move-result').html('<div class="alert alert-info">正在移动 ' + (idx) + '/' + keys.length + '：' + esc(it.name) + '…</div>');
      if (noSlash(newPath) === oldPath) { skipped++; return next(); }
      ajaxForm('move', { oldPath: oldPath, newPath: noSlash(newPath) }).then(function () {
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
      // 把增强面板放到工具栏下方；移动弹窗与提示层挂到 body
      var toolbar = $('.btn-toolbar').first();
      if (toolbar.length) { toolbar.after(HTML_PANEL); }
      else { $('.container').first().append(HTML_PANEL); }
      $('body').append(HTML_OVERLAY).append(HTML_MOVE_MODAL);

      $('#oka-current-path').text('/');
      setInterval(function () { $('#oka-current-path').text(curPath()); }, 500);

      // 捕获阶段接管拖放
      document.addEventListener('dragenter', onDragEnter, true);
      document.addEventListener('dragover', onDragOver, true);
      document.addEventListener('dragleave', onDragLeave, true);
      document.addEventListener('drop', onDrop, true);
      window.addEventListener('dragend', hideDragUI, true);

      $('#oka-tasks-clear').on('click', function () {
        if (running) { return; }
        tasks = []; renderTasks(); tasksVisible(false); $('#oka-result').empty();
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
      $('#oka-move-open').on('click', function () {
        renderMoveItems();
        $('#oka-move-result').empty();
        $('#oka-move-modal').modal('show');
        loadBrowse(curPath());
      });
      $('#oka-move-confirm').on('click', doMove);
      $(document).on('click', '#oka-move-crumb a, #oka-move-browser a.oka-dir', function (e) {
        e.preventDefault();
        loadBrowse($(this).attr('data-dir'));
      });

      bindSelection();
      renderTasks();
    } catch (e) {
      // 增强层任何异常都不能把原页面带崩：只留一条可见提示
      try {
        $('.container').first().prepend(
          '<div class="alert alert-warning">OkaWiFi 增强层初始化失败，原页面功能不受影响：' + esc(e && e.message ? e.message : e) + '</div>'
        );
      } catch (ignored) {}
    }
  });
})();
