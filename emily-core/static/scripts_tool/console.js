// Emily 脚本控制台 — 前端逻辑
// ① 加载脚本列表 → 左侧菜单渲染
// ② 选择脚本 → 加载 params schema → 动态表单
// ③ 表单变更 → 实时 CLI 等效命令预览
// ④ 提交 → 校验 → 写库二次确认 → POST /api/v1/scripts/run

const API = '/api/v1/scripts';

let _schema = null;        // 当前脚本的 form_schema
let _list = [];            // 全部脚本列表
let _selectedName = null;  // 当前选中
let _pendingValues = null; // 写库确认前暂存的表单值

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

    el.innerHTML = filtered.map(s => {
        const badges = [];
        if (s.has_params) badges.push('<span class="badge badge-form">表单</span>');
        if (s.writes_db) badges.push('<span class="badge badge-db">写库</span>');
        if (s.has_check) badges.push('<span class="badge badge-check">自检</span>');
        const active = s.name === _selectedName ? ' active' : '';
        return `<div class="script-item${active}" data-name="${s.name}">
            <div class="sname">
                ${s.name}
                <span class="badge badge-cat">${s.category}</span>
            </div>
            <div class="badges">${badges.join(' ')}</div>
            <div class="scat">${escapeHtml(trunc(s.description, 50))}</div>
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
    _pendingValues = null;

    // 高亮
    q('#script-list').querySelectorAll('.script-item').forEach(el => {
        el.classList.toggle('active', el.dataset.name === name);
    });

    showEmpty(false);
    q('#runner').hidden = false;
    q('#param-form').innerHTML = '<div class="hint" style="padding:20px">加载参数…</div>';
    q('#script-name').textContent = name;

    const resp = await fetch(`${API}/schema/${encodeURIComponent(name)}`);
    const json = await resp.json();
    if (json.code !== 0 || !json.data) {
        q('#param-form').innerHTML = `<div class="hint">加载失败: ${json.message}</div>`;
        return;
    }

    _schema = json.data;
    q('#script-desc').textContent = _schema.description || '';

    // badges
    const badges = q('#script-badges');
    const parts = [];
    if (_schema.writes_db) parts.push('<span class="badge badge-db">写数据库</span>');
    if (_schema.check_arg) parts.push(`<span class="badge badge-check">自检 ${_schema.check_arg}</span>`);
    parts.push(`<span class="badge badge-cat">超时 ${_schema.timeout_seconds}s</span>`);
    badges.innerHTML = parts.join(' ');

    renderParamsForm(_schema.params || []);
}

// ── 渲染参数表单 ──

function renderParamsForm(params) {
    const el = q('#param-form');
    if (!params.length) {
        el.innerHTML = '<div class="hint" style="padding:16px">该脚本无参数，直接执行即可。</div>';
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

// ── 收集表单值 ──

function collectValues() {
    const params = _schema.params || [];
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

function buildPreview(params, values) {
    const parts = [];
    params.forEach(p => {
        const v = values[p.name];
        if (v == null) return;
        if (p.positional) { parts.unshift(String(v)); return; }
        if (p.type === 'flag') { parts.push('--' + p.name); return; }
        parts.push('--' + p.name, String(v));
    });
    const name = _schema ? _schema.name : '';
    return `uv run python scripts/${name}.py ${parts.join(' ')}`;
}

function updateCliPreview() {
    const params = _schema ? _schema.params || [] : [];
    const values = collectValues();
    q('#cli-preview').textContent = buildPreview(params, values);
}

// ── 执行 ──

q('#btn-run').addEventListener('click', async () => {
    if (!_schema) return;
    const values = collectValues();
    const hasParams = Object.keys(values).length > 0;

    if (_schema.writes_db && hasParams) {
        // 写库脚本第一次 → 先跑 check_arg 预览，弹出确认
        _pendingValues = values;
        q('#confirm-name').textContent = _schema.name;
        q('#confirm-cmd').textContent = buildPreview(_schema.params || [], values);
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
function escapeHtml(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}
function escapeAttr(s) {
    return String(s).replace(/"/g, '&quot;');
}
function trunc(s, n) {
    if (!s) return '';
    return s.length <= n ? s : s.slice(0, n) + '…';
}
function showEmpty(show) {
    q('#empty-state').hidden = !show;
    q('#runner').hidden = show;
}

// ── 启动 ──

loadList();