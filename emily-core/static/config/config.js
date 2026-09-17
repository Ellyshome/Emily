/* emy-config 配置中心 —— 只读展示 + 前端内存草稿导出。
 *
 * 边界（与后端一致）：
 *   · 不写盘：草稿只存在内存，刷新即失效（AC-US-04.4）；本页无任何写配置的请求。
 *   · 运行期可改项归 emy-console，本页不重复实现（AC-US-08.3）。
 */

const API = '/api/v1/config';

// 固定导航项 + 动态配置分组
const NAV_FIXED = [
    { key: 'overview', label: '总览' },
    { key: 'findings', label: '差异告警' },
    { key: 'files', label: '配置文件' },
    { key: 'env', label: '环境变量' },
];

const SOURCE_LABEL = {
    env: '环境变量',
    'dotenv-uninjected': '声明未注入',
    default: '代码默认',
};
const SOURCE_CLASS = {
    env: 'src-env',
    'dotenv-uninjected': 'src-uninjected',
    default: 'src-default',
};

let INV = null;                 // 清单数据
let current = 'overview';       // 当前导航项
let query = '';                 // 顶部筛选词
const draft = new Map();        // 字段名 -> 目标值（仅内存）

// ── DOM 工具 ──────────────────────────────────────────────────────────────
function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
}
function $(id) { return document.getElementById(id); }

function matchQuery(...texts) {
    if (!query) return true;
    const q = query.toLowerCase();
    return texts.some((t) => (t || '').toLowerCase().includes(q));
}

// 值单元渲染：空 / 密钥 / 普通
function cellNode(cell, extraCls) {
    if (!cell || !cell.present) {
        return el('span', 'cell-empty', '—');
    }
    const cls = (cell.display.startsWith('已配置（') ? 'cell-secret ' : '') + (extraCls || '');
    return el('span', cls.trim(), cell.display);
}

// ── 加载 ──────────────────────────────────────────────────────────────────
async function load() {
    try {
        const resp = await fetch(`${API}/inventory`);
        const body = await resp.json();
        if (body.code !== 0 || !body.data) {
            showError(body.message || '清单接口返回异常');
            return;
        }
        INV = body.data;
        renderBadges();
        renderNav();
        renderMain();
        $('generated-at').textContent = `清单生成：${INV.runtime.generated_at}`;
    } catch (e) {
        showError(`加载配置清单失败：${e}`);
    }
}

function showError(msg) {
    $('badges').replaceChildren(el('span', 'badge badge-err', '清单不可用'));
    $('nav').replaceChildren(el('div', 'hint', '—'));
    const main = $('main');
    main.replaceChildren();
    const p = el('div', 'panel');
    p.append(el('div', 'panel-title', '无法加载配置清单'));
    p.append(el('div', 'hint', msg));
    p.append(el('div', 'hint', '清单接口为只读聚合，失败不影响 Emily 运行。'));
    main.append(p);
}

function renderBadges() {
    const box = $('badges');
    box.replaceChildren();
    const rt = INV.runtime;
    const sum = INV.finding_summary;
    box.append(el('span', 'badge ' + (rt.dotenv_readable ? 'badge-ok' : 'badge-warn'),
        rt.dotenv_readable ? '.env 可读' : '.env 不可读'));
    const warnBadge = el('span', 'badge ' + (sum.warn ? 'badge-warn' : 'badge-ok'), `告警 ${sum.warn}`);
    box.append(warnBadge);
    box.append(el('span', 'badge badge-info', `信息 ${sum.info}`));
    box.append(el('span', 'badge', `字段 ${rt.field_count}`));
    box.append(el('span', 'badge', `环境变量入口 ${rt.env_entry_count}`));
}

// ── 导航 ──────────────────────────────────────────────────────────────────
function navItem(key, label, count, warnCount) {
    const btn = el('button', 'navitem' + (current === key ? ' active' : ''));
    btn.type = 'button';
    btn.append(el('span', null, label));
    if (warnCount) btn.append(el('span', 'count dot', '●'));
    if (count !== undefined && count !== null) btn.append(el('span', 'count', count));
    btn.addEventListener('click', () => { current = key; renderNav(); renderMain(); });
    return btn;
}

function renderNav() {
    const nav = $('nav');
    nav.replaceChildren();
    nav.append(el('div', 'nav-group-title', '视图'));
    NAV_FIXED.forEach((it) => {
        let count = null;
        if (it.key === 'findings') count = INV.findings.length;
        if (it.key === 'files') count = INV.files.length;
        if (it.key === 'env') count = INV.dotenv_declared.length;
        nav.append(navItem(it.key, it.label, count, it.key === 'findings' && INV.finding_summary.warn));
    });
    nav.append(el('div', 'nav-group-title', '配置分组'));
    INV.sections.forEach((s) => {
        nav.append(navItem(`section:${s.key}`, s.title, s.count, false));
    });
}

// ── 主区路由 ──────────────────────────────────────────────────────────────
function renderMain() {
    const main = $('main');
    main.replaceChildren();
    if (current === 'overview') return renderOverview(main);
    if (current === 'findings') return renderFindings(main);
    if (current === 'files') return renderFiles(main);
    if (current === 'env') return renderEnv(main);
    if (current.startsWith('section:')) return renderSection(main, current.slice('section:'.length));
    renderOverview(main);
}

// ── 视图：总览 ────────────────────────────────────────────────────────────
function renderOverview(main) {
    const rt = INV.runtime;
    const sum = INV.finding_summary;

    main.append(el('h2', '总览'));

    const cards = el('div', 'cards');
    const card = (k, v, s) => {
        const c = el('div', 'card');
        c.append(el('div', 'k', k), el('div', 'v', v));
        if (s) c.append(el('div', 's', s));
        return c;
    };
    cards.append(
        card('Config 字段', rt.field_count, '清单完整性口径'),
        card('环境变量入口', `${rt.env_entry_count} / ${rt.env_map_count}`, 'ENV_CONFIG_MAP 单一来源'),
        card('无入口字段', rt.field_count - rt.env_entry_count, '仅代码默认值'),
        card('告警', sum.warn, `信息 ${sum.info} 条`),
        card('容器环境变量', rt.container_env_count, `其中 EMILY_* ${rt.container_emily_env_count}`),
    );
    main.append(cards);

    // .env 可见性
    const envPanel = el('div', 'panel');
    envPanel.append(el('div', 'panel-title', '宿主机 .env 可见性（声明值来源）'));
    const kv1 = el('dl', 'kv');
    const row = (k, v) => { kv1.append(el('dt', null, k), el('dd', null, v)); };
    row('挂载路径', rt.dotenv_path);
    row('可读', rt.dotenv_readable ? '是' : '否');
    if (!rt.dotenv_readable) row('说明', rt.dotenv_error);
    row('声明键数', INV.dotenv_declared.length);
    envPanel.append(kv1);
    if (!rt.dotenv_readable) {
        envPanel.append(el('div', 'hint hint-warn',
            '按 C-03 在三份 docker-compose-*.yml 的 emily-core.volumes 增加只读挂载「./.env:/app/host/.env:ro」后重建容器，'));
        envPanel.append(el('div', 'hint hint-warn',
            '「.env 声明」列与「声明未注入」告警即可用。该挂载不参与容器启动，环境仍由 environment 段决定。'));
    }
    main.append(envPanel);

    // 不生效文件
    const bad = INV.files.filter((f) => f.exists && !f.loaded);
    const filePanel = el('div', 'panel');
    filePanel.append(el('div', 'panel-title', '不生效的配置文件（挂了但不被读取）'));
    if (!bad.length) {
        filePanel.append(el('div', 'hint', '当前没有不被读取的已登记配置文件。'));
    } else {
        bad.forEach((f) => {
            const line = el('div', 'hint hint-warn');
            line.append(el('span', 'mono', f.name), document.createTextNode(` — ${f.note}`));
            filePanel.append(line);
        });
        filePanel.append(el('div', 'hint', '应改位置：.env / compose environment（改完需 docker compose up -d emily-core 重建容器）。'));
    }
    main.append(filePanel);

    // 生效方式说明（C-01 / C-02 是两种不同操作）
    const hintPanel = el('div', 'panel');
    hintPanel.append(el('div', 'panel-title', '改配置的生效方式'));
    INV.restart_hint.forEach((h) => {
        const d = el('div', 'hint');
        d.append(el('span', 'mono', h.action), document.createTextNode(`　← ${h.title}`));
        hintPanel.append(d);
        hintPanel.append(el('div', 'hint muted', h.detail));
    });
    main.append(hintPanel);

    // 与 emy-console 的分工（AC-US-08.3）
    const owner = el('div', 'panel');
    owner.append(el('div', 'panel-title', '分工说明'));
    owner.append(el('div', 'hint',
        '本页只做配置的声明面与生效面对照（只读）。运行期可改项（渠道连通性覆盖、出站静默、交互通道等）不走本页，请走 emy-console。'));
    main.append(owner);

    // 运行时信息
    const rtPanel = el('div', 'panel');
    rtPanel.append(el('div', 'panel-title', '运行时信息'));
    const kv2 = el('dl', 'kv');
    const row2 = (k, v) => { kv2.append(el('dt', null, k), el('dd', null, v)); };
    row2('配置来源', rt.config_source);
    row2('Python', rt.python_version);
    row2('类型集', `bool ${rt.env_type_sets.bool} / int ${rt.env_type_sets.int} / float ${rt.env_type_sets.float} / list ${rt.env_type_sets.list}`);
    row2('清单生成时间', rt.generated_at);
    rtPanel.append(kv2);
    main.append(rtPanel);

    // 告警摘要
    main.append(el('h3', null, '告警摘要'));
    if (!INV.findings.length) {
        main.append(el('div', 'hint', '无差异告警。'));
    } else {
        INV.findings.slice(0, 8).forEach((f) => main.append(findingNode(f, false)));
    }
}

// ── 视图：差异告警 ────────────────────────────────────────────────────────
function findingNode(f, withItems) {
    const box = el('div', `finding ${f.level}`);
    const head = el('div', 'fhead');
    head.append(el('span', 'ftag', f.level === 'warn' ? 'WARN' : 'INFO'));
    head.append(el('span', 'ftitle', f.title));
    head.append(el('span', 'fkind', f.kind));
    box.append(head);
    if (f.detail) box.append(el('div', 'fdetail', f.detail));
    if (withItems !== false && f.items && f.items.length) {
        const ul = el('ul');
        f.items.forEach((it) => {
            const li = el('li');
            li.append(el('span', 'mono', it.field), document.createTextNode('：文件值 '));
            li.append(cellNode(it.file));
            li.append(document.createTextNode(' → 生效值 '));
            li.append(cellNode(it.effective));
            ul.append(li);
        });
        box.append(ul);
    }
    if (f.next) box.append(el('div', 'fnext', f.next));
    return box;
}

function renderFindings(main) {
    main.append(el('h2', '差异告警'));
    const list = INV.findings.filter((f) => matchQuery(f.title, f.detail, f.kind));
    if (!list.length) {
        main.append(el('div', 'hint', query ? '无匹配的告警。' : '无差异告警。'));
        return;
    }
    ['warn', 'info'].forEach((level) => {
        const group = list.filter((f) => f.level === level);
        if (!group.length) return;
        main.append(el('h3', null, level === 'warn' ? `需要处理（${group.length}）` : `提示（${group.length}）`));
        group.forEach((f) => main.append(findingNode(f)));
    });
}

// ── 视图：配置文件 ────────────────────────────────────────────────────────
function renderFiles(main) {
    main.append(el('h2', '配置文件'));

    main.append(el('h3', null, '容器内配置文件（/app/config）'));
    const grid = el('div', 'files');
    INV.files
        .filter((f) => matchQuery(f.name, f.note, f.loader))
        .forEach((f) => {
            const cls = !f.exists ? 'missing' : (f.loaded ? 'loaded' : 'unloaded');
            const card = el('div', `fcard ${cls}`);
            card.append(el('div', 'fname', f.name));
            card.append(el('div', 'fmeta',
                f.exists
                    ? `存在 · ${f.size} 字节 · 修改于 ${f.mtime}`
                    : '不存在（容器内该路径无此文件）'));
            if (f.loaded) {
                card.append(el('div', 'floader', `被运行时读取 → ${f.loader}`));
            } else {
                card.append(el('div', 'fnote', f.note || '不被任何运行时代码读取'));
            }
            const btn = el('button', 'fopen', '查看内容');
            btn.type = 'button';
            btn.addEventListener('click', () => openFile(f.name));
            card.append(btn);
            grid.append(card);
        });
    main.append(grid);

    main.append(el('h3', null, '宿主机侧声明（容器内不可读）'));
    const table = el('table', 'grid');
    const thead = el('thead');
    const htr = el('tr');
    ['文件', '容器内可见路径', '只读挂载', '存在', '说明'].forEach((h) => htr.append(el('th', null, h)));
    thead.append(htr);
    table.append(thead);
    const tbody = el('tbody');
    INV.host_files.filter((f) => matchQuery(f.name, f.note)).forEach((f) => {
        const tr = el('tr');
        tr.append(el('td', 'mono', f.name));
        tr.append(el('td', 'mono muted', f.container_path || '—'));
        tr.append(el('td', null, f.mounted ? '是' : '否'));
        tr.append(el('td', null, f.exists ? '是' : '否'));
        tr.append(el('td', 'muted', f.note));
        tbody.append(tr);
    });
    table.append(tbody);
    main.append(table);
}

// ── 视图：环境变量 ────────────────────────────────────────────────────────
function envTable(rows, headers, renderRow) {
    const table = el('table', 'grid');
    const thead = el('thead');
    const htr = el('tr');
    headers.forEach((h) => htr.append(el('th', null, h)));
    thead.append(htr);
    table.append(thead);
    const tbody = el('tbody');
    rows.forEach((r) => tbody.append(renderRow(r)));
    table.append(tbody);
    return table;
}

function renderEnv(main) {
    const rt = INV.runtime;
    main.append(el('h2', '环境变量'));

    if (!rt.dotenv_readable) {
        main.append(el('div', 'hint hint-warn',
            '.env 不可读：无法给出声明总览与「声明未注入」判定。按 C-03 在 compose 增加只读挂载后重建容器。'));
    }

    main.append(el('h3', null, `.env 声明总览（${INV.dotenv_declared.length} 键）`));
    const declared = INV.dotenv_declared.filter((r) => matchQuery(r.key));
    if (!declared.length) {
        main.append(el('div', 'hint', rt.dotenv_readable ? '无匹配项。' : '不可用（.env 未挂载）。'));
    } else {
        main.append(envTable(declared, ['.env 键', '值', '已注入容器', '映射到 Config'],
            (r) => {
                const tr = el('tr');
                tr.append(el('td', 'mono', r.key));
                tr.append(el('td', null, cellNode(r.value)));
                tr.append(el('td', null, r.injected ? '是' : '否'));
                tr.append(el('td', null, r.mapped ? '是' : '否'));
                return tr;
            }));
    }

    main.append(el('h3', null, `容器内未映射到 Config 的 EMILY_*（${INV.unmapped_env.length}）`));
    if (!INV.unmapped_env.length) {
        main.append(el('div', 'hint', '无。'));
    } else {
        main.append(el('div', 'hint',
            '「未映射」不等于「没人读」——部分变量由代码其它位置直接读 os.environ，需先确认消费方。'));
        const rows = INV.unmapped_env.filter((r) => matchQuery(r.env));
        main.append(envTable(rows, ['环境变量', '容器内值', '说明'],
            (r) => {
                const tr = el('tr');
                tr.append(el('td', 'mono', r.env));
                tr.append(el('td', null, cellNode(r.value)));
                tr.append(el('td', 'muted', r.hint));
                return tr;
            }));
    }

    main.append(el('h3', null, `容器有值但 .env 未声明（${INV.env_without_dotenv.length}）`));
    if (!INV.env_without_dotenv.length) {
        main.append(el('div', 'hint', rt.dotenv_readable ? '无。' : '不可用（.env 未挂载）。'));
    } else {
        main.append(el('div', 'hint', '由 compose / 镜像提供（compose environment 段写死或镜像内置），不来自宿主机 .env。'));
        const rows = INV.env_without_dotenv.filter((r) => matchQuery(r.env));
        main.append(envTable(rows, ['环境变量', '容器内值', '映射到 Config'],
            (r) => {
                const tr = el('tr');
                tr.append(el('td', 'mono', r.env));
                tr.append(el('td', null, cellNode(r.value)));
                tr.append(el('td', null, r.mapped ? '是' : '否'));
                return tr;
            }));
    }
}

// ── 视图：配置分组（三方对照表） ──────────────────────────────────────────
function renderSection(main, sectionKey) {
    const sec = INV.sections.find((s) => s.key === sectionKey);
    main.append(el('h2', null, sec ? sec.title : sectionKey));
    if (sectionKey === 'other') {
        main.append(el('div', 'hint',
            '本组字段没有环境变量入口，只能改代码（config.py 默认值）→ docker restart emily-core。'));
    }
    main.append(fieldTable(sectionKey));
}

function fieldTable(sectionKey) {
    const rows = INV.fields.filter((f) =>
        f.section === sectionKey && matchQuery(f.field, f.env, f.note));

    if (!rows.length) return el('div', 'hint', query ? '无匹配字段。' : '该分组无字段。');

    const table = el('table', 'grid');
    const thead = el('thead');
    const htr = el('tr');
    ['配置项', '环境变量', '代码默认值', '.env 声明', '容器生效值', '来源'].forEach((h) => htr.append(el('th', null, h)));
    thead.append(htr);
    table.append(thead);
    const tbody = el('tbody');
    rows.forEach((f) => {
        tbody.append(fieldRow(f));
        tbody.append(fieldDetailRow(f));
    });
    table.append(tbody);
    return table;
}

function fieldRow(f) {
    const tr = el('tr', 'frow');
    tr.tabIndex = 0;
    tr.setAttribute('role', 'button');
    tr.setAttribute('aria-expanded', 'false');

    const nameTd = el('td');
    nameTd.append(el('span', 'arrow', '▸'));
    nameTd.append(el('span', 'fname', f.field));
    nameTd.append(el('span', 'ftype', f.type));
    // 字段说明直接落在配置项列（AC-US-01.1），不必展开即可读
    nameTd.append(el('div', 'fnote-line', f.note || '（config.py 未解析到该字段说明）'));
    tr.append(nameTd);

    const envTd = el('td');
    if (f.has_env) {
        envTd.append(el('span', 'envname', f.env));
    } else {
        envTd.append(el('span', 'envname-none', '无环境变量入口'));
    }
    tr.append(envTd);

    tr.append(cellTd(f.default, 'cell-default'));
    tr.append(cellTd(f.declared));
    tr.append(cellTd(f.effective));

    const srcTd = el('td');
    srcTd.append(el('span', `src ${SOURCE_CLASS[f.source]}`, SOURCE_LABEL[f.source] || f.source));
    tr.append(srcTd);

    const toggle = () => {
        const detail = tr.nextElementSibling;
        const open = detail.hidden;
        detail.hidden = !open;
        tr.classList.toggle('open', open);
        tr.setAttribute('aria-expanded', String(open));
        tr.querySelector('.arrow').textContent = open ? '▾' : '▸';
    };
    tr.addEventListener('click', toggle);
    tr.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
    return tr;
}

function cellTd(cell, extraCls) {
    const td = el('td');
    td.append(cellNode(cell, extraCls));
    return td;
}

function fieldDetailRow(f) {
    const tr = el('tr', 'fdetail');
    tr.hidden = true;
    const td = el('td');
    td.colSpan = 6;

    td.append(el('div', 'note', f.note || '（config.py 未解析到该字段的说明）'));

    const kv = el('div', 'note muted');
    kv.append(el('span', null, `当前生效值：`));
    kv.append(cellNode(f.effective));
    if (f.restart_required) {
        kv.append(el('span', 'muted', `　·　环境变量驱动，改动需重建容器：docker compose up -d emily-core`));
    } else {
        kv.append(el('span', 'muted', `　·　仅代码默认值，改动需 docker restart emily-core`));
    }
    td.append(kv);

    if (!f.saveable) {
        const row = el('div', 'row');
        row.append(el('span', 'nohint', `不可写入 .env：${f.save_reason}`));
        td.append(row);
        tr.append(td);
        return tr;
    }

    const row = el('div', 'row');
    const label = el('label', null, `目标值（写入 ${f.env}）`);
    label.htmlFor = `draft-${f.field}`;
    const input = document.createElement('input');
    input.type = 'text';
    input.id = `draft-${f.field}`;
    input.placeholder = f.effective.present ? `当前：${f.effective.display}` : '留空表示不设置该变量';
    input.value = draft.get(f.field) || '';
    const preview = el('span', 'preview');
    const refresh = () => {
        const v = input.value.trim();
        if (v) {
            draft.set(f.field, v);
            preview.textContent = `${f.env}=${v}`;
        } else {
            draft.delete(f.field);
            preview.textContent = '（未填写）';
        }
        renderDraftBar();
    };
    input.addEventListener('input', refresh);
    // 展开时恢复既有草稿
    refresh();
    row.append(label, input, preview);
    td.append(row);

    if (f.save_caveat) {
        const caveatRow = el('div', 'row');
        caveatRow.append(el('span', 'nohint', f.save_caveat));
        td.append(caveatRow);
    }

    tr.append(td);
    return tr;
}

// ── 草稿条与导出 ──────────────────────────────────────────────────────────
function renderDraftBar() {
    const bar = $('draftbar');
    $('draft-count').textContent = `草稿 ${draft.size} 项`;
    bar.hidden = draft.size === 0;
}

function buildSnippet() {
    const lines = [];
    lines.push('# emy-config 导出草稿 —— 仅由前端生成，未写入任何文件');
    lines.push(`# 共 ${draft.size} 项`);
    lines.push('#');
    lines.push('# ⚠️ 环境变量在容器「创建」时固化，必须重建容器才生效：');
    lines.push('#     docker compose up -d emily-core');
    lines.push('#    docker restart 不会重读 .env');
    lines.push('');
    draft.forEach((value, field) => {
        const f = INV.fields.find((x) => x.field === field);
        const envName = f ? f.env : field;
        const cur = f && f.effective.present ? f.effective.display : '（未设置，用代码默认值）';
        lines.push(`# ${field}　当前生效值：${cur}`);
        lines.push(`${envName}=${value}`);
        lines.push('');
    });
    return lines.join('\n');
}

async function saveDraft() {
    if (!draft.size) return;
    const items = Array.from(draft.entries()).map(([field, value]) => ({ field, value }));
    const btn = $('draft-save');
    btn.disabled = true;
    btn.textContent = '保存中…';
    try {
        const resp = await fetch(`${API}/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items }),
        });
        const body = await resp.json();
        if (body.code !== 0 || !body.data) {
            openModal('保存失败', body.message || '接口返回异常', '', false);
            return;
        }
        const d = body.data;
        const failed = d.results.filter((r) => !r.ok);
        const lines = d.results.map((r) =>
            `${r.ok ? '✓' : '✗'} ${r.field}${r.env ? `  (${r.env})` : ''} — ${r.message}`);
        openModal(
            `保存结果：成功 ${d.written} 项${failed.length ? `，被拒 ${failed.length} 项` : ''}`,
            d.message,
            lines.join('\n'),
            false,
        );
        // 已写入的项移出草稿；被拒的保留，便于按提示修正
        d.results.filter((r) => r.ok).forEach((r) => draft.delete(r.field));
        renderDraftBar();
        await load();  // 重新拉清单，刷新「.env 声明值 / 来源」
    } catch (e) {
        openModal('保存失败', `请求异常：${e}`, '', false);
    } finally {
        btn.disabled = false;
        btn.textContent = '保存到 .env';
    }
}

function openModal(title, note, text, copyable) {
    $('modal-title').textContent = title;
    const noteNode = $('modal-note');
    noteNode.textContent = note || '';
    noteNode.hidden = !note;
    $('modal-pre').textContent = text;
    const copyBtn = $('modal-copy');
    copyBtn.hidden = !copyable;
    copyBtn.onclick = async () => {
        const ok = await copyText(text);
        copyBtn.textContent = ok ? '已复制' : '复制失败，请手动选择';
        setTimeout(() => { copyBtn.textContent = '复制'; }, 1800);
    };
    $('modal').hidden = false;
}

// 复制：优先 Clipboard API；非安全上下文降级到 execCommand
async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) { /* 落到降级路径 */ }
    }
    try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '-1000px';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        return ok;
    } catch (e) {
        return false;
    }
}

async function openFile(name) {
    openModal(name, '正在读取…', '', false);
    try {
        const resp = await fetch(`${API}/file?name=${encodeURIComponent(name)}`);
        const body = await resp.json();
        if (body.code !== 0 || !body.data) {
            openModal(name, body.message || '读取失败', '', false);
            return;
        }
        const d = body.data;
        const notes = [];
        if (!d.exists) notes.push('容器内该路径不存在此文件');
        if (!d.loaded) notes.push(`不被运行时代码读取：${d.note || '改它不生效'}`);
        else notes.push(`被读取 → ${d.loader}`);
        if (d.truncated) notes.push(`内容超过上限，已截断（原始 ${d.size} 字节）`);
        if (d.error) notes.push(d.error);
        notes.push('只读展示，本页不提供写文件能力。');
        openModal(`${d.name}　${d.size} 字节 · ${d.mtime || '—'}`,
            notes.join('\n'), d.content, true);
    } catch (e) {
        openModal(name, `读取失败：${e}`, '', false);
    }
}

// ── 事件绑定 ──────────────────────────────────────────────────────────────
function bindEvents() {
    $('filter').addEventListener('input', (e) => {
        query = e.target.value.trim();
        if (INV) renderMain();
    });
    $('draft-save').addEventListener('click', saveDraft);
    $('draft-export').addEventListener('click', () => {
        if (!draft.size) return;
        openModal('导出 .env 片段',
            '粘贴到宿主机 .env 后，必须重建容器：docker compose up -d emily-core（docker restart 不重读 .env）。',
            buildSnippet(), true);
    });
    $('draft-clear').addEventListener('click', () => {
        draft.clear();
        renderDraftBar();
        renderMain();
    });
    $('modal-close').addEventListener('click', () => { $('modal').hidden = true; });
    $('modal').addEventListener('click', (e) => {
        if (e.target === $('modal')) $('modal').hidden = true;
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !$('modal').hidden) $('modal').hidden = true;
    });
}

bindEvents();
load();
