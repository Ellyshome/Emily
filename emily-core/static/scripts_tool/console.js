// Emily 脚本控制台 — 前端逻辑
// ① 加载脚本列表 → 左侧菜单渲染
// ② 选择脚本 → 加载 params schema → 动态表单
// ③ 表单变更 → 实时 CLI 等效命令预览
// ④ 提交 → 校验 → 写库二次确认 → POST /api/v1/scripts/run

const API = '/api/v1/scripts';

let _schema = null;        // 当前脚本的 form_schema
let _schemaSub = null;     // 当前选中的子命令（带 subcommands 的脚本）
let _list = [];            // 全部脚本列表
let _selectedName = null;  // 当前选中
let _pendingValues = null; // 写库确认前暂存的表单值

const _optionsCache = {};  // 动态候选值缓存：source → [{value,label,note}]

// ── 加载脚本列表 ──

async function loadList() {
    const display = q('#script-list');
    try {
        display.innerHTML = '<div class="hint">加载中…</div>';
        const resp = await fetch(API + '/list');
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            display.innerHTML = `<div class="hint">加载失败: ${json.message || 'unknown error'}</div>`;
            return;
        }
        _list = json.data.scripts || [];
        renderList(_list);
    } catch (e) {
        display.innerHTML = `<div class="hint">网络错误: ${e.message}</div>`;
    }
}

function renderList(scripts) {
    const el = q('#script-list');
    const filter = q('#filter').value.toLowerCase();
    const filtered = filter
        ? scripts.filter(s => s.name.includes(filter) || s.description.toLowerCase().includes(filter))
        : scripts;

    q('#script-count').textContent = `${filtered.length} / ${scripts.length}`;

    if (!filtered.length) {
        el.innerHTML = '<div class="hint">无匹配脚本</div>';
        return;
    }

    el.innerHTML = filtered.map((s, i) => {
        const active = s.name === _selectedName ? ' active' : '';
        return `<div class="script-item${active}" data-name="${s.name}">
            <span class="sidx">${i + 1}</span>
            <div class="stext">
                <div class="sfunc">${escapeHtml(shortLabel(s))}</div>
                <div class="sname">${escapeHtml(s.name)}</div>
            </div>
        </div>`;
    }).join('');

    // 点击选择
    el.querySelectorAll('.script-item').forEach(item => {
        item.addEventListener('click', () => selectScript(item.dataset.name));
    });
}

// ── 选择脚本 ──

async function selectScript(name) {
    _selectedName = name;
    _schema = null;
    _schemaSub = null;
    _pendingValues = null;

    // 高亮
    q('#script-list').querySelectorAll('.script-item').forEach(el => {
        el.classList.toggle('active', el.dataset.name === name);
    });

    showEmpty(false);
    q('#runner').hidden = false;
    q('#param-form').innerHTML = '<div class="hint" style="padding:20px">加载参数…</div>';
    q('#script-name').textContent = name;
    setRunEnabled(false);  // schema 未就绪前不允许执行

    const resp = await fetch(`${API}/schema/${encodeURIComponent(name)}`);
    const json = await resp.json();
    if (json.code !== 0 || !json.data) {
        q('#param-form').innerHTML = `<div class="hint">加载失败: ${json.message}</div>`;
        return;
    }

    _schema = json.data;
    q('#script-desc').textContent = shortLabel(_schema);

    // badges
    const badges = q('#script-badges');
    const parts = [];
    if ((_schema.params || []).length) parts.push('<span class="badge badge-form">参数表单</span>');
    else parts.push('<span class="badge badge-default">默认参数</span>');
    if (_schema.writes_db) parts.push('<span class="badge badge-db">写数据库</span>');
    if (_schema.check_arg) parts.push(`<span class="badge badge-check">自检 ${_schema.check_arg}</span>`);
    parts.push(`<span class="badge badge-cat">${_schema.category}</span>`);
    parts.push(`<span class="badge badge-cat">超时 ${_schema.timeout_seconds}s</span>`);
    badges.innerHTML = parts.join(' ');

    renderSubcommandBar();
    renderParamsForm(currentParams());
}

// ── 子命令 ──

// 当前生效的参数集：带子命令的脚本用所选子命令的参数
function currentParams() {
    if (!_schema) return [];
    if (_schemaSub) return _schemaSub.params || [];
    return _schema.params || [];
}

function renderSubcommandBar() {
    const box = q('#subcmd-group');
    const subs = (_schema && _schema.subcommands) || [];

    if (!subs.length) {
        box.hidden = true;
        box.innerHTML = '';
        return;
    }

    _schemaSub = subs[0];
    box.hidden = false;
    box.innerHTML = `
        <label class="param-label">动作<span class="param-required">*</span>
            <span class="param-help">（先选动作，再填该动作的参数）</span></label>
        <select id="subcmd-select">
            ${subs.map(s => `<option value="${escapeAttr(s.name)}">${escapeHtml(s.label)}（${escapeHtml(s.name)}）</option>`).join('')}
        </select>
        <div class="param-help" id="subcmd-help" style="display:block;margin-top:6px">${escapeHtml(_schemaSub.help || '')}</div>`;

    q('#subcmd-select').addEventListener('change', ev => {
        _schemaSub = subs.find(s => s.name === ev.target.value) || subs[0];
        q('#subcmd-help').textContent = _schemaSub.help || '';
        renderParamsForm(currentParams());
    });
}

// ── 渲染参数表单 ──

function renderParamsForm(params) {
    const el = q('#param-form');
    setRunEnabled(true);  // schema 已就绪；写库脚本点击时会走二次确认

    if (!params.length) {
        // 与后端 scripts_runner.run_script 一致：无 schema 的脚本按注册表默认参数执行
        const hasSubs = ((_schema && _schema.subcommands) || []).length > 0;
        el.innerHTML = '<div class="hint" style="padding:16px">'
            + (hasSubs
                ? '该动作无需参数，直接执行即可。'
                : '该脚本未声明参数表单，将按注册表默认参数执行。')
            + '</div>';
        updateCliPreview();
        return;
    }

    // 互斥组先聚成一个 radio 组渲染，其余按声明顺序渲染
    const groups = {};
    const singles = [];
    params.forEach(p => {
        if (p.group) {
            (groups[p.group] = groups[p.group] || []).push(p);
        } else {
            singles.push(p);
        }
    });

    const html = [];
    for (const [gname, members] of Object.entries(groups)) {
        html.push(renderMutexGroup(gname, members));
    }
    singles.forEach(p => html.push(renderParamGroup(p)));
    el.innerHTML = html.join('');

    // 绑定：互斥组用 radio（name 统一为组名），其余按 id
    Object.entries(groups).forEach(([gname, members]) => {
        el.querySelectorAll(`input[name="group-${gname}"]`).forEach(r => {
            r.addEventListener('change', updateCliPreview);
        });
    });
    singles.forEach(p => bindParam(p, el));
    updateCliPreview();
    loadDynamicOptions(el, params);
}

function renderMutexGroup(gname, members) {
    const required = members.some(m => m.required);
    const reqMark = required ? '<span class="param-required">*</span>' : '';
    const radios = members.map(m => `<label>
        <input type="radio" name="group-${gname}" id="param-${m.name}" value="${m.name}">
        ${escapeHtml(m.label)}
    </label>`).join('');
    const helps = members.filter(m => m.help)
        .map(m => `${escapeHtml(m.label)} — ${escapeHtml(m.help)}`).join('；');
    return `<div class="param-group">
        <label class="param-label">运行模式${reqMark}
            <span class="param-help">（互斥，只能选一项）</span></label>
        <div class="radio-group">${radios}</div>
        ${helps ? `<div class="param-help" style="display:block;margin-top:6px">${helps}</div>` : ''}
    </div>`;
}

function renderParamGroup(p) {
    const reqMark = p.required ? '<span class="param-required">*</span>' : '';
    const helpHtml = p.help ? `<span class="param-help">${escapeHtml(p.help)}</span>` : '';

    // 动态候选（取值来自真实环境）→ 下拉，选项由 loadDynamicOptions 异步填充
    if (p.options_source) {
        return `<div class="param-group">
            <label class="param-label">${escapeHtml(p.label)}${reqMark}${helpHtml}</label>
            <select id="param-${p.name}" data-source="${escapeAttr(p.options_source)}">
                <option value="">加载中…</option>
            </select>
        </div>`;
    }

    if (p.type === 'flag') {
        return `<div class="param-group inline-flag">
            <input type="checkbox" id="param-${p.name}" ${p.default ? 'checked' : ''}>
            <label for="param-${p.name}" class="param-label">
                ${escapeHtml(p.label)}${reqMark}
                ${helpHtml}
            </label>
        </div>`;
    }

    if (p.type === 'int') {
        const minAttr = p.min != null ? ` min="${p.min}"` : '';
        const maxAttr = p.max != null ? ` max="${p.max}"` : '';
        const valAttr = p.default != null ? ` value="${p.default}"` : '';
        return `<div class="param-group">
            <label class="param-label">${escapeHtml(p.label)}${reqMark}${helpHtml}</label>
            <input type="number" id="param-${p.name}" ${minAttr}${maxAttr}${valAttr}
                   placeholder="${p.help || ''}">
        </div>`;
    }

    if (p.type === 'enum' && p.choices && p.choices.length > 0) {
        if (p.choices.length <= 6) {
            // 少量选项 → radio 按钮
            const radios = p.choices.map(c => `<label>
                <input type="radio" name="radio-${p.name}" value="${escapeHtml(c)}"
                    ${c === p.default ? 'checked' : ''}>
                ${escapeHtml(c)}
            </label>`).join('');
            return `<div class="param-group radio-group-label">
                <label class="param-label">${escapeHtml(p.label)}${reqMark}${helpHtml}</label>
                <div class="radio-group">${radios}</div>
            </div>`;
        }
        // 大量选项 → 下拉框
        const opts = p.choices.map(c => `<option value="${escapeHtml(c)}"
            ${c === p.default ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('');
        return `<div class="param-group">
            <label class="param-label">${escapeHtml(p.label)}${reqMark}${helpHtml}</label>
            <select id="param-${p.name}"><option value="">—</option>${opts}</select>
        </div>`;
    }

    // str (默认)
    const valAttr = p.default ? ` value="${escapeAttr(p.default)}"` : '';
    return `<div class="param-group">
        <label class="param-label">${escapeHtml(p.label)}${reqMark}${helpHtml}</label>
        <input type="text" id="param-${p.name}" ${valAttr} placeholder="${p.help || ''}">
    </div>`;
}

function bindParam(p, parent) {
    // 少选项 enum 是 radio 组，按 name 绑定
    if (p.type === 'enum' && p.choices && p.choices.length > 0 && p.choices.length <= 6) {
        parent.querySelectorAll(`input[name="radio-${p.name}"]`).forEach(r => {
            r.addEventListener('change', updateCliPreview);
        });
        return;
    }
    const el = parent.querySelector(`#param-${p.name}`);
    if (!el) return;
    el.addEventListener('change', updateCliPreview);
    el.addEventListener('input', updateCliPreview);
}

// ── 动态候选值（取自真实运行环境）──

async function loadDynamicOptions(root, params) {
    const dynamic = params.filter(p => p.options_source);
    if (!dynamic.length) return;

    await Promise.all(dynamic.map(async p => {
        const sel = root.querySelector(`#param-${p.name}`);
        if (!sel) return;

        // 同一数据源在一次会话内复用，避免反复查库
        let options = _optionsCache[p.options_source];
        if (!options) {
            try {
                const resp = await fetch(`${API}/options/${encodeURIComponent(p.options_source)}`);
                const json = await resp.json();
                if (json.code !== 0 || !json.data) {
                    throw new Error(json.message || 'unknown');
                }
                options = json.data.options || [];
                _optionsCache[p.options_source] = options;
            } catch (e) {
                sel.innerHTML = `<option value="">加载失败：${escapeHtml(e.message)}</option>`;
                return;
            }
        }

        if (!options.length) {
            sel.innerHTML = '<option value="">（该环境暂无可选项）</option>';
            return;
        }

        const placeholder = p.required ? '请选择…' : '（不填）';
        sel.innerHTML = `<option value="">${placeholder}</option>`
            + options.map(o => `<option value="${escapeAttr(o.value)}">${escapeHtml(o.label)}`
                + `${o.note ? ' · ' + escapeHtml(o.note) : ''}</option>`).join('');
        if (p.default) sel.value = String(p.default);
        updateCliPreview();
    }));
}

// ── 收集表单值 ──

function collectValues() {
    const params = currentParams();
    const values = {};

    // 互斥组：读组内选中的 radio，其 value 即参数名
    const groups = {};
    params.forEach(p => {
        if (p.group) (groups[p.group] = groups[p.group] || []).push(p);
    });
    for (const gname of Object.keys(groups)) {
        const sel = document.querySelector(`input[name="group-${gname}"]:checked`);
        if (sel) values[sel.value] = true;
    }

    // 非组参数
    params.forEach(p => {
        if (p.group) return;  // 已由互斥组处理

        // 少选项 enum 渲染成 radio 组，按 name 读取
        if (p.type === 'enum' && p.choices && p.choices.length > 0 && p.choices.length <= 6) {
            const sel = document.querySelector(`input[name="radio-${p.name}"]:checked`);
            if (sel && sel.value !== '') values[p.name] = sel.value;
            return;
        }

        const el = document.querySelector(`#param-${p.name}`);
        if (!el) return;
        if (p.type === 'flag') {
            if (el.checked) values[p.name] = true;
        } else if (p.type === 'int') {
            if (el.value !== '') values[p.name] = parseInt(el.value, 10);
        } else { // str / enum(下拉)
            if (el.value !== '') values[p.name] = el.value;
        }
    });

    return values;
}

// ── CLI 预览 ──

function updateCliPreview() {
    if (!_schema) {
        q('#cli-preview').textContent = '—';
        return;
    }

    const hasSubs = (_schema.subcommands || []).length > 0;
    const params = currentParams();

    if (!params.length && !hasSubs) {
        // 无 schema 脚本：实际执行的是注册表 run_args 声明的默认参数
        const args = (_schema.run_args || []).join(' ');
        q('#cli-preview').textContent =
            (_schema.invocation || '').replace('{args}', args).trim() || '—';
        return;
    }

    const values = collectValues();
    const parts = [];
    params.forEach(p => {
        const v = values[p.name];
        if (v == null) return;
        if (p.positional) { parts.unshift(String(v)); return; }
        if (p.type === 'flag') { parts.push('--' + p.name); return; }
        parts.push('--' + p.name, String(v));
    });

    const sub = (hasSubs && _schemaSub) ? _schemaSub.name + ' ' : '';
    q('#cli-preview').textContent =
        `uv run python scripts/${_schema.name}.py ${sub}${parts.join(' ')}`.trim();
}

// ── 执行 ──

q('#btn-run').addEventListener('click', async () => {
    if (!_schema) return;
    const values = collectValues();

    if (_schema.writes_db) {
        // 写库脚本一律先确认，确认后才带 confirm_write=true 真跑
        _pendingValues = values;
        q('#confirm-name').textContent = _schema.name;
        q('#confirm-cmd').textContent = q('#cli-preview').textContent;
        q('#confirm-overlay').classList.add('active');
        return;
    }

    await doRun(values);
});

q('#confirm-cancel').addEventListener('click', () => {
    q('#confirm-overlay').classList.remove('active');
    _pendingValues = null;
});

q('#confirm-ok').addEventListener('click', async () => {
    q('#confirm-overlay').classList.remove('active');
    if (_pendingValues) {
        await doRun(_pendingValues, true);
        _pendingValues = null;
    }
});

async function doRun(values, confirmWrite = false) {
    const status = q('#run-status');
    status.textContent = '执行中…';

    const elResult = q('#result-body');
    elResult.className = 'result-body';
    elResult.textContent = '';

    const meta = q('#result-meta');
    meta.textContent = '';

    const btn = q('#btn-run');
    btn.disabled = true;

    try {
        const resp = await fetch(`${API}/run/${encodeURIComponent(_schema.name)}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                values: values,
                confirm_write: confirmWrite,
                subcommand: _schemaSub ? _schemaSub.name : null,
            }),
        });
        const json = await resp.json();

        if (json.code !== 0 || !json.data) {
            elResult.textContent = 'API 错误: ' + (json.message || 'unknown');
            elResult.className = 'result-body error';
            return;
        }

        const data = json.data;

        // meta 行
        const parts = [];
        if (data.forced_preview) parts.push('⚠ 预览模式（未确认执行）');
        if (data.returncode != null) parts.push(`退出码: ${data.returncode}`);
        if (data.success) parts.push('✅ 成功');
        else if (data.returncode != null) parts.push('❌ 失败');
        if (data.cli_args) parts.push(`argv: ${data.cli_args.join(' ')}`);
        meta.textContent = parts.join(' | ');

        // 内容
        const output = [];
        if (data.stdout) output.push(data.stdout);
        if (data.stderr) output.push('── STDERR ──\n' + data.stderr);
        if (!data.stdout && !data.stderr) output.push(data.error || '(无输出)');
        elResult.textContent = output.join('\n');

        if (data.success) elResult.className = 'result-body success';
        else if (data.returncode != null) elResult.className = 'result-body error';
        else elResult.className = 'result-body';

        if (data.forced_preview) elResult.className = 'result-body preview';

    } catch (e) {
        elResult.textContent = '网络错误: ' + e.message;
        elResult.className = 'result-body error';
    } finally {
        btn.disabled = false;
        status.textContent = '';
    }
}

// ── 重置 ──

q('#btn-reset').addEventListener('click', () => {
    if (_schema) {
        renderParamsForm(_schema.params || []);
    }
});

// ── 筛选 ──

q('#filter').addEventListener('input', () => {
    renderList(_list);
});

// ── 工具 ──

function q(sel) { return document.querySelector(sel); }

// "执行"按钮可用性：schema 就绪前（加载中/加载失败）禁用，其余放行；
// 写库脚本不在按钮上拦，改为点击后统一弹二次确认。
function setRunEnabled(enabled) {
    const btn = q('#btn-run');
    btn.disabled = !enabled;
    btn.title = enabled ? '' : '脚本信息尚未加载完成';
}
function escapeHtml(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}
function escapeAttr(s) {
    return String(s).replace(/"/g, '&quot;');
}
// 功能名：description 约定为 "<脚本名>.py — 中文功能描述"，
// 列表与详情标题只展示去掉重复前缀后的纯功能描述。
function shortLabel(s) {
    let d = (s && s.description) || '';
    const prefix = (s && s.name ? s.name : '') + '.py';
    if (d.startsWith(prefix)) {
        d = d.slice(prefix.length).replace(/^\s*[—\-–]+\s*/, '');
    }
    return d.replace(/。\s*$/, '').trim();
}
function showEmpty(show) {
    q('#empty-state').hidden = !show;
    q('#runner').hidden = show;
}

// ── 环境信息（真实环境 / Docker 部署实况）──

async function loadEnv() {
    const bar = q('#envbar');
    try {
        const resp = await fetch(API + '/env');
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            bar.innerHTML = '<span class="env-item">环境信息不可用</span>';
            return;
        }
        renderEnv(json.data);
    } catch (e) {
        bar.innerHTML = `<span class="env-item">环境探测失败：${escapeHtml(e.message)}</span>`;
    }
}

function renderEnv(env) {
    const db = env.database || {};
    const counts = env.counts || {};
    const runtime = env.runtime || {};
    const core = env.core || {};
    const containers = env.containers || [];
    const parts = [];

    // 有真实用户与项目才判定为真实环境，否则视为空库
    const hasRealData = (counts.users || 0) > 0 && (counts.projects || 0) > 0;
    if (!db.connected) {
        parts.push('<span class="env-badge env-down">数据库未连接</span>');
    } else if (hasRealData) {
        parts.push('<span class="env-badge env-prod">真实环境</span>');
    } else {
        parts.push('<span class="env-badge env-empty">空库</span>');
    }

    if (db.host) {
        parts.push(`<span class="env-item" title="数据库来源（不含账号口令）">`
            + `${escapeHtml(db.host)}:${db.port || ''}/${escapeHtml(db.database || '')}`
            + ` · ${db.tables || 0} 表</span>`);
    }

    if (counts.users != null) {
        parts.push(`<span class="env-item">用户 ${counts.users} · 项目 ${counts.projects}`
            + ` · 节点 ${counts.nodes}</span>`);
    }

    const running = containers.filter(c => c.state === 'running').length;
    const self = containers.find(c => c.id === runtime.hostname);
    const title = containers.map(c => `${c.name} ${c.state}`).join(' / ');
    parts.push(`<span class="env-item" title="${escapeAttr(title)}">容器 ${running}/${containers.length} 运行`
        + `${self ? ' · ' + escapeHtml(self.name) + ' ' + escapeHtml(self.status) : ''}</span>`);

    if (core.status) {
        parts.push(`<span class="env-item">Core ${escapeHtml(core.status)}`
            + ` · 已运行 ${formatUptime(core.uptime_seconds)}</span>`);
    }

    q('#envbar').innerHTML = parts.join('');
}

function formatUptime(seconds) {
    const s = Number(seconds) || 0;
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    const hours = Math.floor(s / 3600);
    const minutes = Math.floor((s % 3600) / 60);
    return minutes ? `${hours}h${minutes}m` : `${hours}h`;
}

// ── 启动 ──

loadEnv();
loadList();