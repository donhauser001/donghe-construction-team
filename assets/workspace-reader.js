(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const localFile = location.protocol === 'file:';
  let data = { project: {}, lines: [], documents: [], diagnostics: [] };
  let activeTab = 'current';
  let busy = false;
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function statusKind(status) {
    const value = String(status || '').toLowerCase();
    if (/待|阻塞|blocked|waiting|pending|部分|未通过|未完成|未验收/.test(value)) return 'wait';
    if (/已完成|已通过|已归档|已验收|done|complete|archived|closed|accepted/.test(value)) return 'done';
    return 'unknown';
  }
  function statusLabel(status) {
    const labels = { active: '进行中', in_progress: '进行中', planned: '已安排', pending: '待处理', waiting: '等待', blocked: '阻塞', done: '已完成', completed: '已完成', archived: '已归档', unknown: '状态未明' };
    return labels[status] || status || '状态未明';
  }
  const badge = status => el('span', statusLabel(status), 'badge ' + statusKind(status));
  function notice(message, error = false) {
    $('notice').textContent = message;
    $('notice').classList.toggle('error', error);
  }
  function diagnosticLabel(value) {
    if (typeof value === 'string') return value;
    const labels = {
      unresolved_focus: '任务引用了尚未找到来源的任务线',
      invalid_task_record: '任务记录格式无法识别',
      symlink_skipped: '已跳过外部链接目录',
      unreadable_or_symlink: '资料无法读取或属于外部链接',
      document_count_limit: '资料数量超过本次读取上限，部分内容未收录',
      document_size_limit: '文档超过单文件读取上限，尚未收录',
      total_size_limit: '资料总量超过本次读取上限，部分内容未收录',
      invalid_utf8: '文档编码无法识别'
    };
    return `${labels[value.code] || value.message || '资料读取提示'}${value.path || value.id ? '：' + (value.path || value.id) : ''}`;
  }
  function normalize(next) {
    if (!next || typeof next !== 'object' || !next.project || !Array.isArray(next.lines) || !Array.isArray(next.documents)) throw new Error('资料格式不完整');
    return { ...next, diagnostics: Array.isArray(next.diagnostics) ? next.diagnostics : [] };
  }
  function openDocument(path) {
    const [documentPath, encodedAnchor = ''] = String(path || '').split('#');
    let anchor;
    try { anchor = decodeURIComponent(encodedAnchor); } catch { anchor = encodedAnchor; }
    const doc = data.documents.find(item => item.path === documentPath);
    $('reader-title').textContent = doc?.title || '资料未收录';
    $('reader-path').textContent = path || '';
    $('reader-body').replaceChildren();
    renderMarkdown(doc?.content || '当前快照未收录该资料。连接实时资料并重建索引后再查看。', documentPath);
    if (!$('reader').open) $('reader').showModal();
    $('reader').scrollTop = 0;
    if (anchor) {
      const target = [...$('reader-body').querySelectorAll('[id]')].find(node => node.id === anchor);
      if (target) target.scrollIntoView({ block: 'start' });
    }
  }
  function resolveDocument(href, basePath) {
    if (/^[a-z][a-z0-9+.-]*:|^\/\//i.test(href)) return null;
    const [raw, anchor] = href.split('#');
    if (!raw) return basePath + (anchor ? '#' + anchor : '');
    const parts = raw.startsWith('/') ? [] : String(basePath || '').split('/').slice(0, -1);
    let decoded;
    try { decoded = decodeURIComponent(raw); } catch { decoded = raw; }
    for (const part of decoded.split('/')) {
      if (part === '..') parts.pop();
      else if (part && part !== '.') parts.push(part);
    }
    return parts.join('/') + (anchor ? '#' + anchor : '');
  }
  function inline(parent, text, basePath) {
    const pattern = /\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`/g;
    let end = 0;
    for (const match of text.matchAll(pattern)) {
      parent.append(document.createTextNode(text.slice(end, match.index)));
      if (match[1]) {
        const target = resolveDocument(match[2], basePath);
        if (target !== null) {
          const link = el('button', match[1], 'text-link');
          link.type = 'button';
          link.addEventListener('click', () => openDocument(target));
          parent.append(link);
        } else if (/^https?:\/\//i.test(match[2])) {
          const link = el('a', match[1]);
          link.href = match[2]; link.target = '_blank'; link.rel = 'noopener noreferrer';
          parent.append(link);
        } else parent.append(document.createTextNode(match[1]));
      } else parent.append(el(match[3] ? 'strong' : 'code', match[3] || match[4]));
      end = match.index + match[0].length;
    }
    parent.append(document.createTextNode(text.slice(end)));
  }
  function renderMarkdown(content, path) {
    const root = $('reader-body');
    let paragraph = [], code = null, list = null;
    const headingIds = new Map();
    const flush = () => {
      if (paragraph.length) { const p = el('p'); inline(p, paragraph.join('\n'), path); root.append(p); paragraph = []; }
      list = null;
    };
    const lines = String(content).split('\n');
    const tableCells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|'));
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index];
      if (/^\s*```/.test(line)) {
        if (code !== null) { root.append(el('pre', code.join('\n'))); code = null; }
        else { flush(); code = []; }
        continue;
      }
      if (code !== null) { code.push(line); continue; }
      const explicitAnchor = /^\s*<a\s+(?:id|name)=["']([^"']+)["']\s*>\s*<\/a>\s*$/.exec(line);
      if (explicitAnchor) { flush(); const marker = el('span'); marker.id = explicitAnchor[1]; root.append(marker); continue; }
      if (line.includes('|') && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1])) {
        flush();
        const wrap = el('div', undefined, 'table-scroll');
        const table = el('table'); const head = el('thead'); const row = el('tr');
        for (const cell of tableCells(line)) { const th = el('th'); th.scope = 'col'; inline(th, cell, path); row.append(th); }
        head.append(row); table.append(head);
        const body = el('tbody'); index++;
        while (index + 1 < lines.length && lines[index + 1].trim() && lines[index + 1].includes('|')) {
          const tr = el('tr');
          for (const cell of tableCells(lines[++index])) { const td = el('td'); inline(td, cell, path); tr.append(td); }
          body.append(tr);
        }
        table.append(body); wrap.append(table); root.append(wrap); continue;
      }
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      const item = /^\s*(?:[-*+] |\d+\. )(.+)$/.exec(line);
      if (heading) {
        flush(); const h = el('h' + heading[1].length); inline(h, heading[2], path);
        const key = h.textContent.toLowerCase().replace(/[^\p{L}\p{N}_\-\s]/gu, '').trim().replace(/\s/g, '-');
        const occurrence = headingIds.get(key) || 0; headingIds.set(key, occurrence + 1);
        h.id = key + (occurrence ? '-' + occurrence : ''); root.append(h);
      }
      else if (item) {
        if (!list) { flush(); list = el('ul'); root.append(list); }
        const li = el('li'); inline(li, item[1], path); list.append(li);
      } else if (/^>\s?/.test(line)) { flush(); const quote = el('blockquote'); inline(quote, line.replace(/^>\s?/, ''), path); root.append(quote); }
      else if (!line.trim()) flush();
      else { list = null; paragraph.push(line); }
    }
    flush();
    if (code !== null) root.append(el('pre', code.join('\n')));
  }
  function documentButton(path, label = '查看原文与证据') {
    const button = el('button', label, 'text-link');
    button.type = 'button';
    button.addEventListener('click', () => openDocument(path));
    return button;
  }
  function lineCard(line) {
    const card = el('article', undefined, 'line-card');
    const heading = el('div', undefined, 'line-heading');
    heading.append(el('h2', line.title || line.id || '未命名任务线'), badge(!line.status || line.status === 'unknown' ? '状态见任务' : line.status));
    card.append(heading, el('p', line.id || '待归类', 'line-id'));
    const flow = el('ol', undefined, 'flow');
    for (const task of line.tasks || []) {
      const item = el('li', undefined, 'task ' + statusKind(task.status));
      const details = el('details');
      const summary = el('summary');
      summary.append(el('span', task.id || '', 'task-id'), el('span', task.title || '未命名任务', 'task-title'), badge(task.status));
      const content = el('div', undefined, 'task-content');
      content.append(el('p', task.excerpt || '展开原文查看目标、最新执行记录与验收依据。'));
      if (task.path) content.append(documentButton(task.path));
      details.append(summary, content); item.append(details); flow.append(item);
    }
    if (!flow.children.length) flow.append(el('li', '当前资料中未找到明确关联的任务卡。', 'notice'));
    card.append(flow);
    if (line.sourcePath) { const source = el('p'); source.append(documentButton(line.sourcePath, '查看任务线来源')); card.append(source); }
    return card;
  }
  function renderResults(query) {
    $('lines').replaceChildren(); $('documents').replaceChildren();
    const search = query.trim().toLowerCase();
    const docsMode = activeTab === 'documents' || activeTab === 'history' || Boolean(search);
    $('lines').hidden = docsMode; $('documents').hidden = !docsMode;
    if (docsMode) {
      const matches = data.documents.filter(doc => search ? [doc.title, doc.path, doc.content].some(value => String(value || '').toLowerCase().includes(search)) : activeTab !== 'history' || doc.archived);
      $('overview').textContent = search ? `全文搜索：找到 ${matches.length} 份资料（包含当前与历史）` : `共 ${matches.length} 份${activeTab === 'history' ? '历史' : ''}资料，点击阅读原文`;
      for (const doc of matches) {
        const item = el('article', undefined, 'doc-item');
        const title = el('h3'); title.append(documentButton(doc.path, doc.title || doc.path));
        item.append(title, el('p', `${doc.archived ? '历史档案 · ' : '当前资料 · '}${doc.path}`));
        const text = String(doc.content || '');
        const position = Math.max(0, text.toLowerCase().indexOf(search));
        item.append(el('p', text.slice(Math.max(0, position - 45), position + 180), 'snippet'));
        $('documents').append(item);
      }
      if (!matches.length) $('documents').append(el('p', '未找到匹配资料。可换一个关键词，或连接助手后重建索引。', 'empty'));
      return;
    }
    const lines = data.lines;
    const assigned = lines.filter(line => line.id !== 'unclassified');
    const unclassifiedCount = lines.filter(line => line.id === 'unclassified').reduce((count, line) => count + (line.tasks || []).length, 0);
    $('overview').textContent = `${assigned.length} 条当前任务线${unclassifiedCount ? ` · ${unclassifiedCount} 个待归类任务` : ''} · 展开任务查看执行摘要与原文`;
    for (const line of lines) $('lines').append(lineCard(line));
    if (!lines.length) $('lines').append(el('p', '当前范围没有已收录的任务线。资料缺少归属时，应补充来源关系，不能据此推断任务已结束。', 'empty'));
  }
  function render() {
    $('project-name').textContent = data.project.name || '项目现场';
    document.title = `${data.project.name || '项目'} · 东合项目进化史`;
    const time = new Date(data.generatedAt);
    const validTime = Number.isFinite(time.getTime());
    $('updated-at').textContent = validTime ? `资料读取于 ${time.toLocaleString('zh-CN')}` : '快照时间未知';
    $('current-count').textContent = data.lines.filter(line => line.id !== 'unclassified').length;
    $('history-count').textContent = data.documents.filter(doc => doc.archived).length;
    $('document-count').textContent = data.documents.length;
    $('connect').href = `donghe://workspace?id=${encodeURIComponent(data.project.id || '')}`;
    $('connect').hidden = !localFile;
    $('diagnostics').replaceChildren();
    const diagnostics = [...data.diagnostics];
    if (!validTime || Date.now() - time.getTime() > 24 * 60 * 60 * 1000) diagnostics.unshift('当前快照可能陈旧，请连接实时资料后刷新。');
    for (const message of diagnostics) $('diagnostics').append(el('p', diagnosticLabel(message)));
    $('diagnostics').hidden = diagnostics.length === 0;
    renderResults($('search').value);
  }
  async function request(action) {
    if (localFile) {
      notice('这是可离线阅读的快照。请点击“连接实时资料”唤起东合助手，再在实时页面刷新；本页不会运行脚本。');
      return;
    }
    if (busy) return;
    busy = true;
    $('refresh').disabled = true; $('reindex').disabled = true;
    notice(action === 'reindex' ? '正在重新扫描资料并建立全文索引……' : '正在读取项目最新资料……');
    try {
      const response = await fetch(`/api/${action}`, {
        method: action === 'snapshot' ? 'GET' : 'POST',
        headers: { 'X-Donghe-Token': data.bridge?.token || '' },
        cache: 'no-store',
        signal: AbortSignal.timeout(120000)
      });
      if (!response.ok) throw new Error(`读取失败（${response.status}）`);
      data = normalize(await response.json());
      render();
      $('connection-state').textContent = '实时资料已连接';
      notice('已读取落盘资料。刷新不会续跑施工、登记完工或代替裁决。');
    } catch (error) {
      $('connection-state').textContent = '连接中断 · 保留快照';
      $('connect').hidden = false;
      notice(`更新未完成：${error.message}。当前仍展示上一次成功读取的资料；可点击“连接实时资料”重新唤起助手。`, true);
    } finally { busy = false; $('refresh').disabled = false; $('reindex').disabled = false; }
  }
  $('search').addEventListener('input', () => renderResults($('search').value));
  for (const tab of ['current', 'history', 'documents']) $('tab-' + tab).addEventListener('click', () => {
    activeTab = tab; $('search').value = '';
    for (const name of ['current', 'history', 'documents']) {
      $('tab-' + name).classList.toggle('selected', tab === name);
      $('tab-' + name).setAttribute('aria-pressed', String(tab === name));
    }
    renderResults('');
  });
  $('close-reader').addEventListener('click', () => $('reader').close());
  $('refresh').addEventListener('click', () => request('refresh'));
  $('reindex').addEventListener('click', () => request('reindex'));
  try {
    data = normalize(JSON.parse($('workspace-data')?.textContent || '{}'));
    render();
    notice(localFile ? '当前为离线快照。点击“连接实时资料”打开本机阅读助手，无需启动命令。' : '正在连接本机资料……');
    if (!localFile) request('snapshot');
  } catch (error) { notice(`快照无法读取：${error.message}。请重新生成项目阅读入口。`, true); }
})();
