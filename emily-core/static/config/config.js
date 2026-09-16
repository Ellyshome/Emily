'use strict';

/* emy-config 配置中心 —— 只读展示页。
   数据源：GET /api/v1/config/inventory（清单）+ /api/v1/config/file?name=（单文件内容）。
   页面不写盘：草稿只存在于前端内存，刷新即失效。 */

const state = {
    inv: null,
    view: 'overview',
    filter: '',
    drafts: new Map(),    // env 名 -> 目标值（内存草稿）
    expanded: new Set(),  // 展开详情的字段名
};

const $ = (id) => document.getElementById(id);

const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

const SOURCE_LABEL = { env: '环境变量', 'dotenv-uninjected': '声明未注入', default: '代码默认' };

async function api(path) {
    const resp = await fetch(path, { cache: 'no-store' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const body = await resp.json();
    if (body.code !== 0) throw new Error(body.message || '接口返回失败');
    return body.data;
}

function matches(text) {
    if (!state.filter) return true;
    return String(text || '').toLowerCase().includes(state.filter.toLowerCase());
}

/* ── 顶栏徽标 / 左导航 / 草稿条 ── */

function renderBadges() {
    const rt = state.inv.runtime;
    const warns = state.inv.findings.filter((f) => f.level === 'warn').length;
    const infos = state.inv.findings.filter((f) => f.level === 'info').length;
    const dotenv = rt.dotenv_readable
        ? `<span class="badge badge-ok" title="${esc(rt.dotenv_path)}">.env 可读（${rt.dotenv_key_count} 键）</span>`
        : '<span class="badge badge-warn" title="宿主机 .env 未挂载，声明值列不可用">.env 不可读</span>';
    $('badges').innerHTML = dotenv
        + (warns ? `<span class="badge badge-warn">告警 ${warns}</span>` : '<span class="badge badge-ok">无 warn 告警</span>')
        + (infos ? `<span class="badge badge-info">提示 ${infos}</span>` : '');
}

function renderNav() {
    const inv = state.inv;
    const warns = inv.findings.filter((f) => f.level === 'warn').length;
    const items = [
        { key: 'overview', title: '总览', count: '' },
        { key: 'findings', title: '差异告警', count: warns || '' },
        { key: 'files', title: '配置文件', count: inv.files.length },
        { key: 'env', title: '环境变量', count: inv.dotenv_declared.length || '' },
    ];
    let html = items.map((it) => navHtml(it.key, it.title, it.count)).join('');
    html += '<div class="nav-group-title">配置分组</div>';
    html += inv.sections.map((s) => navHtml('section:' + s.key, s.title, s.count)).join('');
    $('nav').innerHTML = html;

    $('sidebar-foot').innerHTML = `字段 ${inv.runtime.field_count} · 环境变量入口 ${inv.runtime.env_entry_count}`
        + `<br>清单生成：${esc(inv.runtime.generated_at)}`;
}

function navHtml(key, title, count) {
    const active = state.view === key ? ' active' : '';
    return `<button type="button" class="nav-item${active}" data-view="${esc(key)}">`
        + `<span>${esc(title)}</span><span class="nav-count">${esc(count)}</span></button>`;
}

function renderDraftBar() {
    const bar = $('draftbar');
    if (!state.drafts.size) { bar.hidden = true; return; }
    bar.hidden = false;
    $('draft-count').textContent = `草稿 ${state.drafts.size} 项（仅存于本页内存，刷新即失效）`;
}

/* ── 主区视图 ── */

function render() {
    renderBadges();
    renderNav();
    renderDraftBar();
    const inv = state.inv;
    let html = '';
    if (state.view === 'overview') html = overviewHtml(inv);
    else if (state.view === 'findings') html = findingsHtml(inv);
    else if (state.view === 'files') html = filesHtml(inv);
    else if (state.view === 'env') html = envHtml(inv);
    else if (state.view.startsWith('section:')) html = sectionHtml(inv, state.view.slice('section:'.length));
    $('main').innerHTML = html;
    bindMain();
}

function bindMain() {
    document.querySelectorAll('.nav-item').forEach((el) => {
        el.addEventListener('click', () => { state.view = el.dataset.view; render(); });
    });
    document.querySelectorAll('tr.field-row').forEach((row) => {
        const toggle = () => {
            const name = row.dataset.field;
            if (state.expanded.has(name)) state.expanded.delete(name); else state.expanded.add(name);
            render();
            focusField(name);
        };
        row.addEventListener('click', toggle);
        row.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
        });
    });
    document.querySelectorAll('.target-input').forEach((input) => {
        input.addEventListener('input', () => {
            const env = input.dataset.env;
            if (input.value.trim()) state.drafts.set(env, input.value.trim());
            else state.drafts.delete(env);
            renderDraftBar();
            const preview = document.querySelector(`.preview[data-env="${CSS.escape(env)}"]`);
            if (preview) preview.textContent = `将写入：${env}=${input.value.trim()}`;
        });
    });
    document.querySelectorAll('[data-file]').forEach((btn) => {
        btn.addEventListener('click', () => openFile(btn.dataset.file));
    });
}

function focusField(name) {
    const row = document.querySelector(`tr.field-row[data-field="${CSS.escape(name)}"]`);
    if (row) row.focus();
}

function statCards(cards) {
    return `<div class="cards">${cards.map((c) => `
        <div class="card">
            <div class="card-label">${esc(c.label)}</div>
            <div class="card-value">${esc(c.value)}</div>
            ${c.note ? `<div class="card-note">${esc(c.note)}</div>` : ''}
        </div>`).join('')}</div>`;
}

function overviewHtml(inv) {
    const rt = inv.runtime;
    const warns = inv.findings.filter((f) => f.level === 'warn');
    const notLoaded = inv.files.filter((f) => f.exists && !f.read_by_runtime);
    const cards = [
        { label: 'Config 字段', value: rt.field_count, note: '三方对照逐字段覆盖' },
        { label: '环境变量入口', value: rt.env_entry_count, note: 'ENV_CONFIG_MAP 单一来源' },
        { label: '容器环境变量', value: rt.container_env_count, note: '进程可见 env 总数' },
        { label: '.env 声明键', value: rt.dotenv_readable ? rt.dotenv_key_count : '不可读', note: rt.dotenv_readable ? rt.dotenv_path : '未挂载' },
        { label: 'warn 告警', value: warns.length, note: warns.length ? '见「差异告警」' : '无' },
        { label: '不生效配置文件', value: notLoaded.length, note: notLoaded.map((f) => f.name).join('、') || '无' },
    ];

    return `<h2 class="view-title">总览</h2>
    <div class="view-desc">配置来源：${esc(rt.config_source)} · Python ${esc(rt.python_version)} · 清单生成于 ${esc(rt.generated_at)}
    <br>本页只读：不写盘、不自动重启、不做热重载；填写目标值仅生成草稿片段供人工执行。</div>
    ${statCards(cards)}

    <div class="panel"><h3>.env 可见性（声明值来源）</h3>
        <div class="panel-sub">${rt.dotenv_readable
            ? `已读取 ${esc(rt.dotenv_path)}（只读挂载，不参与容器启动）`
            : esc(rt.dotenv_mount_hint)}</div>
        <div class="panel-sub">容器内默认读不到宿主机 .env；「声明值」列反映的是宿主机写的值，是否生效取决于 compose environment 段是否注入。</div>
    </div>

    <div class="panel"><h3>改动生效方式</h3>
        <table><thead><tr><th>改动对象</th><th>命令</th><th>说明</th></tr></thead><tbody>
        ${inv.restart_hint.map((h) => `<tr><td>${esc(h.change)}</td>
            <td class="cell-mono">${esc(h.action)}</td><td>${esc(h.note)}</td></tr>`).join('')}
        </tbody></table>
    </div>

    <div class="panel"><h3>告警摘要</h3>
        <div class="panel-sub">完整列表见「差异告警」。每条告警给出可执行的下一步。</div>
        ${inv.findings.length ? inv.findings.slice(0, 6).map(findingHtml).join('') : '<div class="hint">无告警</div>'}
        ${inv.findings.length > 6 ? `<div class="hint">…还有 ${inv.findings.length - 6} 条</div>` : ''}
    </div>`;
}

function findingHtml(f) {
    return `<div class="finding ${esc(f.level)}">
        <div class="f-head"><span class="tag ${f.level === 'warn' ? 'tag-warn' : 'tag-none'}">${esc(f.level)}</span>
            <span>${esc(f.title)}</span><span class="f-kind">${esc(f.kind)}</span></div>
        <div class="f-detail">${esc(f.detail)}</div>
        <div class="f-action">→ ${esc(f.action)}</div>
    </div>`;
}

function findingsHtml(inv) {
    const groups = ['warn', 'info'].map((level) => {
        const list = inv.findings.filter((f) => f.level === level);
        if (!list.length) return '';
        return `<div class="panel"><h3>${level === 'warn' ? '需要处置（warn）' : '仅提示（info）'} · ${list.length} 条</h3>
            ${list.map(findingHtml).join('')}</div>`;
    }).join('');
    return `<h2 class="view-title">差异告警</h2>
    <div class="view-desc">口径：dotenv-uninjected = .env 声明了内核该读的变量但容器里没有；file-conflict = 配置文件键值与生效值不一致；
    file-not-loaded = 已登记文件不被任何运行时代码读取；env-without-dotenv / env-unmapped = 容器内变量的来源与消费方提示。
    差异比对前已做归一化（true/True、1/1 视为同值）。</div>
    ${groups || '<div class="hint">无告警</div>'}`;
}

function filesHtml(inv) {
    const cards = inv.files.filter((f) => matches(f.name)).map((f) => {
        const status = !f.exists ? '<span class="tag tag-none">不存在</span>'
            : f.read_by_runtime ? '<span class="tag tag-env">被运行时读取</span>'
                : '<span class="tag tag-warn">不被读取</span>';
        const evidence = f.evidence.length
            ? `<div class="fc-meta">加载位置：${f.evidence.map((e) => esc(`${e.file}:${e.line}`)).join('、')}</div>`
            : '';
        return `<div class="file-card">
            <div class="fc-head"><span class="cell-mono">${esc(f.name)}</span>${status}</div>
            <div class="fc-meta">路径 ${esc(f.path)}${f.exists ? ` · ${f.size} 字节 · 修改于 ${esc(f.mtime)}` : ''}</div>
            ${evidence}
            ${!f.exists ? '' : (f.read_by_runtime ? '' : `<div class="fc-action">→ ${esc(f.not_loaded_hint)}</div>`)}
            ${f.exists ? `<button type="button" data-file="${esc(f.name)}">查看内容</button>` : ''}
        </div>`;
    }).join('');
    const host = inv.host_files.map((h) => `<tr><td class="cell-mono">${esc(h.name)}</td>
        <td>${esc(h.note)}</td><td>${h.readable ? '可读' : '容器内不可读'}</td></tr>`).join('');
    return `<h2 class="view-title">配置文件</h2>
    <div class="view-desc">容器内配置目录：${esc(inv.runtime.config_file_dir)}。
    「是否被读取」由源码 AST 扫描判定（跳过注释与 docstring），不是硬编码结论。</div>
    ${cards || '<div class="hint">无匹配文件</div>'}
    <div class="panel"><h3>宿主机侧声明（容器内不可读）</h3>
        <table><thead><tr><th>文件</th><th>说明</th><th>本容器可读性</th></tr></thead><tbody>${host}</tbody></table>
    </div>`;
}

function envHtml(inv) {
    const declared = inv.dotenv_declared.filter((d) => matches(d.key)).map((d) => {
        const state_ = d.injected ? '<span class="tag tag-env">已注入</span>'
            : d.mapped ? '<span class="tag tag-warn">未注入</span>' : '<span class="tag tag-none">非本容器配置</span>';
        return `<tr><td class="cell-mono">${esc(d.key)}</td>
            <td class="cell-mono">${d.mapped_field ? esc(d.mapped_field) : '<span class="cell-empty">—</span>'}</td>
            <td>${state_}</td>
            <td class="cell-mono">${esc(d.display) || '<span class="cell-empty">（空）</span>'}</td></tr>`;
    }).join('');
    const unmapped = inv.unmapped_env.filter((u) => matches(u.key)).map((u) => {
        const consumers = u.consumers.length
            ? u.consumers.map((c) => esc(`${c.file}:${c.line}`)).join('、')
            : '<span class="cell-empty">本次扫描未发现代码引用（不等于没人读）</span>';
        return `<tr><td class="cell-mono">${esc(u.key)}</td>
            <td class="cell-mono">${esc(u.display) || '<span class="cell-empty">（空）</span>'}</td>
            <td class="cell-mono">${consumers}</td></tr>`;
    }).join('');
    return `<h2 class="view-title">环境变量</h2>
    <div class="view-desc">「声明」= 宿主机 .env；「注入」= 容器环境中真实存在（由 compose environment 段决定）。
    .env 写了但 compose 未列 → 声明未注入，改它不生效。密钥类只显示长度，不显示明文。</div>
    <div class="panel"><h3>.env 声明总览 · ${inv.dotenv_declared.length} 键</h3>
        ${inv.runtime.dotenv_readable
            ? `<table><thead><tr><th>键</th><th>映射字段</th><th>是否注入容器</th><th>值</th></tr></thead><tbody>${declared || '<tr><td colspan="4" class="hint">无匹配</td></tr>'}</tbody></table>`
            : `<div class="hint">${esc(inv.runtime.dotenv_mount_hint)}</div>`}
    </div>
    <div class="panel"><h3>容器内未映射到 Config 的 EMILY_* · ${inv.unmapped_env.length} 个</h3>
        <div class="panel-sub">这些变量不在 ENV_CONFIG_MAP 中，故改 .env 前需先确认谁在消费（下表为源码引用位置）。</div>
        <table><thead><tr><th>键</th><th>容器值</th><th>代码引用位置</th></tr></thead><tbody>${unmapped || '<tr><td colspan="3" class="hint">无</td></tr>'}</tbody></table>
    </div>`;
}

function sectionHtml(inv, key) {
    const section = inv.sections.find((s) => s.key === key);
    const rows = inv.fields.filter((f) => f.section === key
        && (matches(f.field) || matches(f.env) || matches(f.note)));
    const body = rows.map(fieldRowHtml).join('');
    return `<h2 class="view-title">${esc(section ? section.title : key)}</h2>
    <div class="view-desc">三方对照：代码默认值（config.py） · .env 声明（宿主机） · 容器生效值（当前进程）。
    点任意行展开说明与草稿填写；来源为「声明未注入」表示 .env 里写了但容器没拿到，改它不生效。</div>
    <table><thead><tr>
        <th>配置项</th><th>环境变量</th><th>代码默认值</th><th>.env 声明</th><th>容器生效值</th><th>来源</th>
    </tr></thead><tbody>${body || '<tr><td colspan="6" class="hint">无匹配字段</td></tr>'}</tbody></table>`;
}

function cellClass(cell) {
    return 'cell-mono' + (cell.present ? '' : ' cell-empty');
}

function cellText(cell) {
    return cell.present ? esc(cell.display) : '（空）';
}

function fieldRowHtml(f) {
    const tag = f.source === 'env' ? 'tag-env' : (f.source === 'dotenv-uninjected' ? 'tag-warn' : 'tag-default');
    const envCell = f.has_env ? esc(f.env) : '<span class="tag tag-none">无环境变量入口</span>';
    const opened = state.expanded.has(f.field);
    const row = `<tr class="field-row" tabindex="0" role="button" data-field="${esc(f.field)}" title="点击展开详情">
        <td><div class="cell-mono">${esc(f.field)}</div><div class="hint">${esc(f.type)}</div></td>
        <td class="cell-mono">${envCell}</td>
        <td class="${cellClass(f.default)}">${cellText(f.default)}</td>
        <td class="${cellClass(f.declared)}">${cellText(f.declared)}</td>
        <td class="${cellClass(f.effective)}">${cellText(f.effective)}</td>
        <td><span class="tag ${tag}">${esc(SOURCE_LABEL[f.source] || f.source)}</span></td>
    </tr>`;
    if (!opened) return row;
    const draft = state.drafts.get(f.env) || '';
    const editor = f.has_env
        ? `<div class="k">目标值</div><div class="v">
                <input class="target-input" data-env="${esc(f.env)}" value="${esc(draft)}"
                       placeholder="填入后生成 ENV=value 草稿（不写盘）">
                <div class="preview" data-env="${esc(f.env)}">${draft ? `将写入：${esc(f.env)}=${esc(draft)}` : '尚未填写'}</div>
            </div>
            <div class="k">生效方式</div><div class="v">写入宿主机 .env（或 compose environment 段）后执行
                <span class="cell-mono">docker compose -f docker-compose-napcat.yml up -d emily-core</span>（重建容器，docker restart 不重读 .env）</div>`
        : `<div class="k">目标值</div><div class="v">该字段无环境变量入口，只能改 <span class="cell-mono">emily-core/emily_core/config.py</span> 默认值
                （改代码后 <span class="cell-mono">docker restart emily-core</span>）</div>`;
    return row + `<tr class="detail-row"><td colspan="6">
        <div class="detail-grid">
            <div class="k">字段说明</div><div class="v">${esc(f.note) || '<span class="cell-empty">（源码中未解析到说明）</span>'}</div>
            <div class="k">容器生效值</div><div class="v cell-mono">${cellText(f.effective)}</div>
            <div class="k">来源</div><div class="v">${esc(SOURCE_LABEL[f.source] || f.source)}</div>
            ${editor}
        </div>
    </td></tr>`;
}

/* ── 弹窗与草稿 ── */

function openModal(title, text, hint) {
    $('modal-title').textContent = title;
    $('modal-body').textContent = text;
    $('modal-hint').textContent = hint || '';
    $('modal').hidden = false;
}

function closeModal() { $('modal').hidden = true; }

async function openFile(name) {
    try {
        const data = await api('/api/v1/config/file?name=' + encodeURIComponent(name));
        if (!data.exists) { openModal(name, '（文件不存在）', data.path); return; }
        const hint = data.truncated ? '内容超长已截断（上限 200k 字符）' : `路径：${data.path}（只读，密钥类已掩码）`;
        openModal(name, data.content || '（空文件）', hint);
    } catch (e) {
        openModal(name, '读取失败：' + e.message, '');
    }
}

function exportDrafts() {
    if (!state.drafts.size) return;
    const lines = [
        '# 由 emy-config 生成的草稿片段（页面内存草稿，未写盘）',
        '# 生效方式：写入宿主机 .env 后执行',
        '#   docker compose -f docker-compose-napcat.yml up -d emily-core',
        '# ⚠ 环境变量在容器创建时固化，docker restart 不会重读 .env',
        '',
    ];
    state.drafts.forEach((value, env) => lines.push(`${env}=${value}`));
    openModal('草稿片段（未落盘）', lines.join('\n'), `共 ${state.drafts.size} 项；本页不写任何配置文件，请人工确认后写入 .env`);
}

async function copyModal() {
    const text = $('modal-body').textContent;
    try {
        await navigator.clipboard.writeText(text);
        $('modal-hint').textContent = '已复制到剪贴板';
    } catch (e) {
        const area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        area.remove();
        $('modal-hint').textContent = '已复制（降级路径）';
    }
}

/* ── 初始化 ── */

function bindStatic() {
    let timer = null;
    $('filter').addEventListener('input', (e) => {
        state.filter = e.target.value.trim();
        if (timer) clearTimeout(timer);
        timer = setTimeout(render, 150);
    });
    $('draft-export').addEventListener('click', exportDrafts);
    $('draft-clear').addEventListener('click', () => { state.drafts.clear(); render(); });
    $('modal-close').addEventListener('click', closeModal);
    $('modal-copy').addEventListener('click', copyModal);
    $('modal').addEventListener('click', (e) => { if (e.target === $('modal')) closeModal(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
}

async function main() {
    bindStatic();
    try {
        state.inv = await api('/api/v1/config/inventory');
        render();
    } catch (e) {
        $('main').innerHTML = `<div class="hint">配置清单加载失败：${esc(e.message)}</div>`;
    }
}

main();
