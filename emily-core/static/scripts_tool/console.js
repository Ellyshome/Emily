// emy-console（Emily 脚本控制台）— 前端逻辑
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

// 「消息模拟器」已并入「日志聚合」框架，不再出现在左侧「脚本测试」列表
const MESSAGE_SIMULATOR_NAME = 'emytest_chat';

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
    const filter = q('#filter').value.toLowerCase();
    const filtered = filter
        ? scripts.filter(s => s.name.includes(filter) || s.description.toLowerCase().includes(filter))
        : scripts;

    q('#script-count').textContent = `${filtered.length} / ${scripts.length}`;

    renderModuleList(filter);
    renderScriptList(filtered);
}

function renderModuleList(filter) {
    const el = q('#module-list');
    const kw = (filter || '').toLowerCase();
    const items = kw
        ? MODULE_ITEMS.filter(m =>
            m.func.toLowerCase().includes(kw) ||
            m.sub.toLowerCase().includes(kw) ||
            m.name.toLowerCase().includes(kw))
        : MODULE_ITEMS;
    q('#module-count').textContent = `${items.length} / ${MODULE_ITEMS.length}`;
    el.innerHTML = items.map((m, i) => {
        const active = _selectedName === m.name ? ' active' : '';
        return `<div class="script-item ${m.cls}${active}" data-name="${m.name}">
            <span class="sidx">${i + 1}</span>
            <div class="stext">
                <div class="sfunc">${m.func}</div>
                <div class="sname">${m.sub}</div>
            </div>
        </div>`;
    }).join('');
    bindListClicks(el);
}

function renderScriptList(scripts) {
    const el = q('#script-list');
    const visible = scripts.filter(s => s.name !== MESSAGE_SIMULATOR_NAME);
    q('#group-script-count').textContent = visible.length;
    if (!visible.length) {
        el.innerHTML = '<div class="hint">无匹配脚本</div>';
        return;
    }
    el.innerHTML = visible.map((s, i) => {
        const active = s.name === _selectedName ? ' active' : '';
        return `<div class="script-item${active}" data-name="${s.name}">
            <span class="sidx">${i + 1}</span>
            <div class="stext">
                <div class="sfunc">${escapeHtml(shortLabel(s))}</div>
                <div class="sname">${escapeHtml(s.name)}</div>
            </div>
        </div>`;
    }).join('');
    bindListClicks(el);
}

function bindListClicks(el) {
    // 点击选择：模块条目走 selectXxx，其余脚本走 selectScript
    el.querySelectorAll('.script-item').forEach(item => {
        item.addEventListener('click', () => {
            if (item.dataset.name === RESOURCE_ENTRY_NAME) {
                selectResource();
            } else if (item.dataset.name === UPLOAD_ENTRY_NAME) {
                selectUpload();
            } else if (item.dataset.name === RAG_ENTRY_NAME) {
                selectRag();
            } else if (item.dataset.name === NODE_TABLE_ENTRY_NAME) {
                selectNodeTable();
            } else if (item.dataset.name === LOG_ENTRY_NAME) {
                selectLogs();
            } else if (item.dataset.name === SELF_CHECK_ENTRY_NAME) {
                selectSelfCheck();
            } else if (item.dataset.name === PROMPT_ENTRY_NAME) {
                selectPrompt();
            } else if (item.dataset.name === LLM_TRACE_ENTRY_NAME) {
                selectLlmTrace();
            } else if (item.dataset.name === MCP_ENTRY_NAME) {
                selectMcp();
            } else if (item.dataset.name === SOP_DISPLAY_ENTRY_NAME) {
                selectSopDisplay();
            } else if (item.dataset.name === PANORAMA_NODES_ENTRY_NAME) {
                selectPanoramaNodes();
            } else if (item.dataset.name === PROJECT_EVENTS_ENTRY_NAME) {
                selectProjectEvents();
            } else if (item.dataset.name === SESSION_POOL_ENTRY_NAME) {
                selectSessionPool();
            } else if (item.dataset.name === SESSION_ARCHIVE_ENTRY_NAME) {
                selectSessionArchive();
            } else if (item.dataset.name === TEST_CASES_ENTRY_NAME) {
                selectTestCases();
            } else {
                selectScript(item.dataset.name);
            }
        });
    });
}

function setActiveEntry(name) {
    // 高亮同时作用于「模块能力」与「脚本测试」两个列表
    [q('#module-list'), q('#script-list')].forEach(list => {
        list.querySelectorAll('.script-item').forEach(el => {
            el.classList.toggle('active', el.dataset.name === name);
        });
    });
    renderModuleCmd(name);
}

// 将当前模块能力项对应的命令展示到右侧面板头部（脚本项无对应则清空）
function renderModuleCmd(name) {
    const item = MODULE_ITEMS.find(m => m.name === name);
    const cmd = item ? item.cmd : '';
    document.querySelectorAll('.module-cmd').forEach(el => {
        el.textContent = cmd;
    });
}

// ── 选择脚本 ──

async function selectScript(name) {
    _selectedName = name;
    _schema = null;
    _schemaSub = null;
    _pendingValues = null;

    // 高亮
    setActiveEntry(name);

    showEmpty(false);
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
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
    q('#cli-source').textContent = _schema.source_path
        ? (_schema.source_path + (_schema.entrypoint ? `  →  ${_schema.entrypoint}` : ''))
        : '—';

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
    _pendingSimulatorValues = null;
});

q('#confirm-ok').addEventListener('click', async () => {
    q('#confirm-overlay').classList.remove('active');
    if (_pendingValues) {
        await doRun(_pendingValues, true);
        _pendingValues = null;
    } else if (_pendingSimulatorValues) {
        await runSimulator(_pendingSimulatorValues, true);
        _pendingSimulatorValues = null;
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

// ── 侧栏折叠 / 拖拽 ──

// 折叠：点击分组标题切换
document.querySelectorAll('.sidebar-group-title').forEach(title => {
    title.addEventListener('click', () => {
        const group = title.closest('.sidebar-group');
        group.classList.toggle('collapsed');
        const key = group.id === 'group-modules' ? 'modules' : 'scripts';
        localStorage.setItem('emy-console-collapse-' + key, group.classList.contains('collapsed') ? '1' : '0');
    });
});

// 恢复上次折叠状态
['modules', 'scripts'].forEach(key => {
    if (localStorage.getItem('emy-console-collapse-' + key) === '1') {
        const group = document.getElementById('group-' + key);
        if (group) group.classList.add('collapsed');
    }
});

// 拖拽：调整侧栏宽度
(function initSidebarResizer() {
    const sidebar = document.querySelector('.sidebar');
    const resizer = document.getElementById('sidebar-resizer');
    if (!sidebar || !resizer) return;
    resizer.addEventListener('mousedown', e => {
        e.preventDefault();
        const startX = e.clientX;
        const startW = sidebar.getBoundingClientRect().width;
        resizer.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        const onMove = ev => {
            const w = Math.min(600, Math.max(220, startW + ev.clientX - startX));
            sidebar.style.width = w + 'px';
        };
        const onUp = () => {
            resizer.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('emy-console-sidebar-width', sidebar.style.width);
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
    const savedW = localStorage.getItem('emy-console-sidebar-width');
    if (savedW) sidebar.style.width = savedW;
})();

// 拖拽：调整模块栏高度
(function initGroupResizer() {
    const group = document.getElementById('group-modules');
    const resizer = document.getElementById('group-resizer');
    if (!group || !resizer) return;
    resizer.addEventListener('mousedown', e => {
        e.preventDefault();
        const startY = e.clientY;
        const startH = group.getBoundingClientRect().height;
        resizer.classList.add('dragging');
        document.body.style.cursor = 'row-resize';
        document.body.style.userSelect = 'none';
        const onMove = ev => {
            const h = Math.min(window.innerHeight * 0.7, Math.max(60, startH + ev.clientY - startY));
            group.style.maxHeight = 'none';
            group.style.height = h + 'px';
        };
        const onUp = () => {
            resizer.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('emy-console-modules-height', group.style.height);
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
    const savedH = localStorage.getItem('emy-console-modules-height');
    if (savedH) {
        group.style.maxHeight = 'none';
        group.style.height = savedH;
    }
})();

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

// ── 资源展示 ──

const API_CONSOLE = '/api/v1/console';
const RESOURCE_ENTRY_NAME = '__resource_display__';  // 左侧脚本列表里的特殊条目
const UPLOAD_ENTRY_NAME = '__upload__';              // 左侧脚本列表里的文件上传条目
const RAG_ENTRY_NAME = '__rag_index__';              // 左侧脚本列表里的 RAG 入库条目
const NODE_TABLE_ENTRY_NAME = '__node_table__';      // 左侧脚本列表里的全景节点表条目
const LOG_ENTRY_NAME = '__logs__';                   // 左侧脚本列表里的日志聚合条目
const SELF_CHECK_ENTRY_NAME = '__self_check__';      // 左侧脚本列表里的系统自检条目
const PROMPT_ENTRY_NAME = '__prompt_engineering__';  // 左侧脚本列表里的 Prompt 工程条目
const LLM_TRACE_ENTRY_NAME = '__llm_trace__';         // 左侧脚本列表里的 LLM 流量条目
const MCP_ENTRY_NAME = '__mcp__';                    // 左侧脚本列表里的 MCP 配置条目
const SOP_DISPLAY_ENTRY_NAME = '__sop_display__';    // 左侧脚本列表里的「现有 SOP 展示」条目
const PANORAMA_NODES_ENTRY_NAME = '__panorama_nodes__'; // 左侧脚本列表里的「参考全景节点」条目
const PROJECT_EVENTS_ENTRY_NAME = '__project_events__';   // 左侧脚本列表里的「项目事件」条目
const SESSION_POOL_ENTRY_NAME = '__session_pool__';       // 左侧脚本列表里的「会话池」条目
const SESSION_ARCHIVE_ENTRY_NAME = '__session_archive__'; // 左侧脚本列表里的「会话归档」条目
const TEST_CASES_ENTRY_NAME = '__test_cases__';           // 左侧脚本列表里的「测试用例库」条目

// ── 全局操作人（左侧栏顶部）：所有右侧栏模块的统一锚点 ──
// 作用：① 控制各模块的可见范围（文件 / 节点 / RAG / 日志）；② 写操作日志归属。

let _globalUsersLoaded = false;
let _globalUsers = [];       // [{value,label,note,level}]
let _userLevelMap = {};      // 用户 id → level（用于密级调整权限判定）

// 人员等级可读标签（对应 emily_core/permission/level.py 的 LEVEL_NAME）
const USER_LEVEL_NAMES = { 1: '访客', 2: '参建执行', 3: '参建管理', 4: '建设主管', 5: '管理员', 6: '系统管理员' };
function userLevelLabel(level) {
    const lv = Number(level) || 0;
    if (!lv) return '';
    return `${USER_LEVEL_NAMES[lv] || '未知'} (L${lv})`;
}

function getGlobalOperator() {
    return q('#global-operator-select').value;
}

async function ensureGlobalUsers() {
    if (_globalUsersLoaded) return;
    try {
        const resp = await fetch(API_CONSOLE + '/users');
        const json = await resp.json();
        if (json.code === 0 && json.data) {
            _globalUsers = json.data.users || [];
            _userLevelMap = {};
            _globalUsers.forEach(u => { _userLevelMap[u.value] = u.level || 0; });
            q('#global-operator-select').innerHTML =
                _globalUsers.map(u => {
                    const lv = userLevelLabel(u.level);
                    return `<option value="${escapeAttr(u.value)}">${escapeHtml(u.label)}${u.note ? ' · ' + escapeHtml(u.note) : ''}${lv ? ' · ' + escapeHtml(lv) : ''}</option>`;
                }).join('');

            // 默认选定 L6 人员（无 L6 时回退到 level 最高者），不保留空选择态
            const l6 = _globalUsers.find(u => (u.level || 0) >= 6);
            const fallback = l6 || _globalUsers.reduce(
                (a, b) => ((a.level || 0) >= (b.level || 0) ? a : b),
                _globalUsers[0],
            );
            if (fallback) {
                q('#global-operator-select').value = fallback.value;
                if (_selectedName) reloadActiveModule();
            }
        }
        _globalUsersLoaded = true;
    } catch (e) {
        // 用户列表加载失败不阻断页面
    }
}

function reloadActiveModule() {
    switch (_selectedName) {
        case RESOURCE_ENTRY_NAME:
            loadResources(getGlobalOperator());
            break;
        case UPLOAD_ENTRY_NAME:
            loadFileMgrFiles();
            loadFileMgrNodes();
            break;
        case RAG_ENTRY_NAME:
            loadRagBackends();
            break;
        case NODE_TABLE_ENTRY_NAME:
            loadNodeTable(getGlobalOperator());
            break;
        case SOP_DISPLAY_ENTRY_NAME:
            loadSopDisplay(getGlobalOperator());
            break;
        case PANORAMA_NODES_ENTRY_NAME:
            loadPanoramaNodes(getGlobalOperator());
            break;
        case PROJECT_EVENTS_ENTRY_NAME:
            loadProjectEvents();
            break;
        case SESSION_POOL_ENTRY_NAME:
            loadSessionPool();
            break;
        case SESSION_ARCHIVE_ENTRY_NAME:
            loadSessionArchive();
            break;
        case TEST_CASES_ENTRY_NAME:
            loadTestCases();
            break;
        case LOG_ENTRY_NAME:
            loadLogs(q('#logs-module-select').value, getGlobalOperator());
            break;
        default:
            break;
    }
}

q('#global-operator-select').addEventListener('change', () => {
    reloadActiveModule();
});


// 左侧「模块能力」分组（序号 1-7）
const MODULE_ITEMS = [
    { name: RESOURCE_ENTRY_NAME, cls: 'resource-entry', func: '资源展示', sub: '四组资源清单',
      cmd: 'GET  /api/v1/console/resources\n# emily-core/api/routes/console_resources.py::get_resources' },
    { name: UPLOAD_ENTRY_NAME, cls: 'upload-entry', func: '文件管理', sub: '上传 · 删除 · 节点/RAG 进出',
      cmd: 'GET  /api/v1/console/files\nPOST /api/v1/console/upload\nPOST /api/v1/console/file-delete\nPOST /api/v1/console/node-file\nPOST /api/v1/console/rag-index\nPOST /api/v1/console/rag-delete\n# emily-core/api/routes/console_resources.py' },
    { name: RAG_ENTRY_NAME, cls: 'rag-entry', func: 'RAG 模块', sub: '查库',
      cmd: 'POST /api/v1/console/rag-search\n# emily-core/api/routes/console_resources.py' },
    { name: NODE_TABLE_ENTRY_NAME, cls: 'node-table-entry', func: '全景节点表', sub: '节点 · 参与人 · 共享文件',
      cmd: 'GET  /api/v1/console/node-table\nPOST /api/v1/console/node-participant\nPOST /api/v1/console/node-file\n# emily-core/api/routes/console_resources.py' },
    { name: LOG_ENTRY_NAME, cls: 'logs-entry', func: '日志聚合', sub: '按人 · 按模块查看日志',
      cmd: 'GET  /api/v1/console/logs\n# emily-core/api/routes/console_resources.py::get_aggregated_logs' },
    { name: SELF_CHECK_ENTRY_NAME, cls: 'self-check-entry', func: '系统自检', sub: '统计 · 工具一致性检查',
      cmd: 'POST /api/v1/console/self-check\n# emily-core/api/routes/console_resources.py::run_self_check' },
    { name: PROMPT_ENTRY_NAME, cls: 'prompt-entry', func: 'Prompt 工程', sub: '提示词模板 · 插入点 · 来源',
      cmd: 'GET  /api/v1/console/prompts\nGET  /api/v1/console/prompt?name=...\n# emily-core/api/routes/console_resources.py' },
    { name: LLM_TRACE_ENTRY_NAME, cls: 'llm-trace-entry', func: 'LLM 流量', sub: 'mitmproxy 抓包 · 请求/响应全文',
      cmd: 'GET  /api/v1/console/llm-trace\n# emily-core/api/routes/console_resources.py::get_llm_trace' },
    { name: MCP_ENTRY_NAME, cls: 'mcp-entry', func: 'MCP 配置', sub: 'Server 增删改 · 开关 · 在线探测',
      cmd: 'GET    /api/v1/console/mcp/servers\nPOST   /api/v1/console/mcp/server\nDELETE /api/v1/console/mcp/server\nPOST   /api/v1/console/mcp/toggle\nPOST   /api/v1/console/mcp/probe\n# emily-core/api/routes/console_resources.py' },
    { name: SOP_DISPLAY_ENTRY_NAME, cls: 'sop-display-entry', func: 'SOP 展示', sub: '现有 SOP 清单',
      cmd: 'GET  /api/v1/console/sops\n# emily-core/api/routes/console_resources.py::get_sops' },
    { name: PANORAMA_NODES_ENTRY_NAME, cls: 'panorama-nodes-entry', func: '参考全景节点', sub: '节点全景只读展示',
      cmd: 'GET  /api/v1/console/node-table\n# emily-core/api/routes/console_resources.py::get_node_table' },
    { name: PROJECT_EVENTS_ENTRY_NAME, cls: 'project-events-entry', func: '项目事件', sub: '会议 · 任务 · 文件 · 流转 · 成果 · 节点事件',
      cmd: 'GET  /api/v1/console/project-events\n# emily-core/api/routes/console_resources.py::get_project_events' },
    { name: SESSION_POOL_ENTRY_NAME, cls: 'session-pool-entry', func: '会话池', sub: '活跃 Session · 空闲时长 · 最近消息',
      cmd: 'GET  /api/v1/console/session-pool\nGET  /api/v1/console/session-pool/{conversation_id}/messages\n# emily-core/api/routes/console_resources.py' },
    { name: SESSION_ARCHIVE_ENTRY_NAME, cls: 'session-archive-entry', func: '会话归档', sub: '已归档会话 · 点会话ID看对话全文',
      cmd: 'GET  /api/v1/console/session-archives\nGET  /api/v1/console/session-archives/{id}/content\n# emily-core/api/routes/console_resources.py' },
    { name: TEST_CASES_ENTRY_NAME, cls: 'test-cases-entry', func: '测试用例库', sub: '回归用例清单 · 点编号看用例细节',
      cmd: 'GET  /api/v1/console/test-cases\nGET  /api/v1/console/test-cases/{case_id}\n# emily-core/api/routes/console_resources.py' },
];

const RESOURCE_GROUPS = ['files', 'nodes', 'sops', 'rag_files'];
const GROUP_CONF = {
    files:     { listId: 'list-files',     countId: 'count-files' },
    nodes:     { listId: 'list-nodes',     countId: 'count-nodes' },
    sops:      { listId: 'list-sops',      countId: 'count-sops' },
    rag_files: { listId: 'list-rag_files', countId: 'count-rag_files' },
};

let _resources = { files: [], nodes: [], sops: [], rag_files: [] };

// 选中左侧「资源展示」条目：右侧显示四组资源，隐藏脚本执行区
function selectResource() {
    _selectedName = RESOURCE_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(RESOURCE_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('.resource-display').hidden = false;

    ensureGlobalUsers();
    loadResources(getGlobalOperator());
}

// ── 文件管理（上传 / 删除 / 节点与 RAG 进出）──

let _fileMgrLoaded = false;
let _fileMgrRows = [];
let _fileMgrSort = { key: null, dir: 1 };   // dir: 1 升序 / -1 降序

function formatSize(bytes) {
    const n = Number(bytes) || 0;
    if (n <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(v >= 100 || i === 0 ? 0 : 1) + ' ' + units[i];
}

function confidentialityLabel(level) {
    const map = { 0: '公开', 1: '内部', 2: '机密' };
    return map[level] ?? String(level ?? 0);
}

function confidentialitySelect(r) {
    const cur = (r.confidentiality === 0 || r.confidentiality === 1 || r.confidentiality === 2)
        ? r.confidentiality : 1;
    const opts = [0, 1, 2].map(v =>
        `<option value="${v}"${v === cur ? ' selected' : ''}>${confidentialityLabel(v)}</option>`
    ).join('');
    return `<select class="file-mgr-conf-select" data-file="${escapeAttr(r.id)}" title="仅上传人本人或 L5/L6 可调整">${opts}</select>`;
}

function canEditConfidentiality(row) {
    const operatorId = getGlobalOperator();
    if (!operatorId) return false;
    if (row.uploaded_by === operatorId) return true;
    return (_userLevelMap[operatorId] || 0) >= 5;
}

// 选中左侧「文件管理」条目：右侧显示文件管理面板，隐藏其他
function selectUpload() {
    _selectedName = UPLOAD_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(UPLOAD_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#upload-panel').hidden = false;

    ensureFileMgrData();
}

async function ensureFileMgrData() {
    if (!_fileMgrLoaded) {
        try {
            await loadFileMgrDropdowns();
            _fileMgrLoaded = true;
        } catch (e) {
            // 下拉数据加载失败不阻断文件管理面板
        }
    }
    loadFileMgrFiles();
}

async function loadFileMgrDropdowns() {
    await ensureGlobalUsers();
    try {
        const backendsResp = await fetch(API_CONSOLE + '/rag/backends');
        const backendsJson = await backendsResp.json();
        if (backendsJson.code === 0 && backendsJson.data) {
            applyBackendOptions(q('#file-mgr-backend'), backendsJson.data, 'file-mgr-backend');
        }
    } catch (e) {
        // 后端探测失败保持默认
    }
    await loadFileMgrNodes();
}

async function loadFileMgrNodes() {
    const userId = getGlobalOperator();
    const query = userId ? ('?user_id=' + encodeURIComponent(userId)) : '';
    try {
        const resp = await fetch(API_CONSOLE + '/node-table' + query);
        const json = await resp.json();
        if (json.code === 0 && json.data) {
            const nodes = json.data.rows || [];
            q('#file-mgr-node-select').innerHTML = '<option value="">请选择目标节点…</option>'
                + nodes.map(n =>
                    `<option value="${escapeAttr(n.node_id)}">${escapeHtml(n.node_name || n.node_id)}</option>`
                ).join('');
        }
    } catch (e) {
        // 节点加载失败静默
    }
}

async function loadFileMgrFiles() {
    try {
        const userId = getGlobalOperator();
        const query = userId ? ('?user_id=' + encodeURIComponent(userId)) : '';
        const resp = await fetch(API_CONSOLE + '/files' + query);
        const json = await resp.json();
        if (json.code === 0 && json.data) {
            renderFileMgrTable(json.data.rows || []);
            renderFileMgrPath(json.data.storage);
        }
    } catch (e) {
        // 文件清单加载失败静默
    }
}

function renderFileMgrPath(storage) {
    const el = q('#file-mgr-path');
    if (!el) return;
    if (!storage) { el.textContent = ''; return; }
    el.textContent = `实际路径 · 容器 ${storage.container} · 端口 ${storage.port}`
        + ` · 容器内 ${storage.dir_container} · 宿主机 ${storage.dir_host}`;
}

function fileMgrSortValue(row, key) {
    switch (key) {
        case 'name':
            return (row.name || '').toLowerCase();
        case 'id':
            return (row.file_no || row.id || '').toLowerCase();
        case 'uploader':
            return (row.uploaded_by_name || row.uploaded_by || '').toLowerCase();
        case 'time': {
            const ts = Date.parse((row.created_at || '').replace(' ', 'T'));
            return Number.isFinite(ts) ? ts : 0;
        }
        case 'size':
            return Number(row.file_size) || 0;
        case 'conf': {
            // 密级列固定优先级：自己上传 > 公开(0) > 内部(1) > 机密(2)
            const isSelf = (row.uploaded_by && row.uploaded_by === getGlobalOperator()) ? 0 : 1;
            const level = [0, 1, 2].includes(Number(row.confidentiality)) ? Number(row.confidentiality) : 1;
            return isSelf * 100 + level;
        }
        default:
            return '';
    }
}

function fileMgrCompare(a, b) {
    const { key, dir } = _fileMgrSort;
    if (!key) return 0;
    const va = fileMgrSortValue(a, key);
    const vb = fileMgrSortValue(b, key);
    let cmp;
    if (typeof va === 'number' && typeof vb === 'number') {
        cmp = va - vb;
    } else {
        cmp = String(va).localeCompare(String(vb), 'zh-CN');
    }
    return cmp * dir;
}

function updateFileMgrSortIndicators() {
    document.querySelectorAll('.file-mgr-table th.sortable').forEach(th => {
        const ind = th.querySelector('.sort-ind');
        if (!ind) return;
        ind.textContent = (th.getAttribute('data-sort-key') === _fileMgrSort.key)
            ? (_fileMgrSort.dir === 1 ? ' ▲' : ' ▼')
            : '';
    });
}

function renderFileMgrTable(rows) {
    _fileMgrRows = rows || [];
    const tbody = q('#file-mgr-tbody');

    // 记录当前选中，排序重渲染后恢复
    const checked = new Set(selectedFileIds());

    const list = _fileMgrRows.slice();
    if (_fileMgrSort.key) {
        list.sort(fileMgrCompare);
    }
    updateFileMgrSortIndicators();

    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="muted">暂无文件</td></tr>';
        return;
    }

    tbody.innerHTML = list.map(r => {
        const nodes = (r.visible_nodes || [])
            .map(n => escapeHtml(n.name || n.node_id)).join('、') || '—';
        const checkedAttr = checked.has(r.id) ? ' checked' : '';
        return `<tr>
            <td class="col-check"><input type="checkbox" class="file-mgr-check" value="${escapeAttr(r.id)}"${checkedAttr}></td>
            <td title="${escapeAttr(r.name)}">${escapeHtml(r.name)}</td>
            <td class="mono" title="${escapeAttr(r.id)}">${escapeHtml(r.file_no || r.id)}</td>
            <td>${canEditConfidentiality(r) ? confidentialitySelect(r) : escapeHtml(confidentialityLabel(r.confidentiality))}</td>
            <td>${escapeHtml(r.uploaded_by_name || r.uploaded_by || '')}</td>
            <td class="mono">${escapeHtml(r.created_at)}</td>
            <td class="mono">${formatSize(r.file_size)}</td>
            <td class="col-rag">
                <input type="checkbox" class="file-mgr-rag-check" value="${escapeAttr(r.id)}"${r.in_rag ? ' checked' : ''} title="勾选=入库，取消=出库">
            </td>
            <td>${nodes}</td>
        </tr>`;
    }).join('');
}

function selectedFileIds() {
    return Array.from(document.querySelectorAll('.file-mgr-check:checked')).map(cb => cb.value);
}

function setFileMgrStatus(msg) {
    q('#file-mgr-status').textContent = msg;
}

async function doUpload() {
    const userId = getGlobalOperator();
    const fileInput = q('#upload-file-input');
    const status = q('#upload-status');
    const btn = q('#upload-btn');

    if (!userId) {
        status.textContent = '请选择操作人';
        return;
    }
    if (!fileInput.files || !fileInput.files.length) {
        status.textContent = '请选择要上传的文件';
        return;
    }

    const file = fileInput.files[0];
    const form = new FormData();
    form.append('user_id', userId);
    form.append('file', file);
    form.append('confidentiality', q('#upload-conf-select').value);

    btn.disabled = true;
    status.textContent = '上传中…';
    try {
        const resp = await fetch(API_CONSOLE + '/upload', { method: 'POST', body: form });
        const json = await resp.json();
        if (json.code === 0) {
            status.textContent = `上传成功：${json.data.filename}（${json.data.file_no}）`;
            fileInput.value = '';
            loadFileMgrFiles();
        } else {
            status.textContent = `上传失败：${json.message || 'unknown'}`;
        }
    } catch (e) {
        status.textContent = `上传失败：${e.message}`;
    } finally {
        btn.disabled = false;
    }
}

async function doFileMgrDelete() {
    const ids = selectedFileIds();
    const operatorId = getGlobalOperator();
    if (!ids.length) { setFileMgrStatus('请先选择文件'); return; }
    if (!operatorId) { setFileMgrStatus('请选择操作人（用于日志归属）'); return; }
    if (!window.confirm(`确认删除选中的 ${ids.length} 个文件？将同时清理其 RAG 分块与节点关联，不可撤销。`)) {
        return;
    }
    let ok = 0, fail = 0;
    for (let i = 0; i < ids.length; i++) {
        try {
            const resp = await fetch(API_CONSOLE + '/file-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_id: ids[i], operator_id: operatorId }),
            });
            const json = await resp.json();
            if (json.code === 0) ok++; else fail++;
        } catch (e) { fail++; }
        setFileMgrStatus(`删除中（${i + 1}/${ids.length}）…`);
    }
    setFileMgrStatus(`删除完成：成功 ${ok}，失败 ${fail}`);
    loadFileMgrFiles();
}

async function doFileMgrNode(action) {
    const ids = selectedFileIds();
    const operatorId = getGlobalOperator();
    const nodeId = q('#file-mgr-node-select').value;
    const verb = action === 'add' ? '进入' : '移除';
    if (!ids.length) { setFileMgrStatus('请先选择文件'); return; }
    if (!operatorId) { setFileMgrStatus('请选择操作人'); return; }
    if (!nodeId) { setFileMgrStatus('请选择目标节点'); return; }

    let ok = 0, fail = 0;
    for (let i = 0; i < ids.length; i++) {
        try {
            const resp = await fetch(API_CONSOLE + '/node-file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, node_id: nodeId, file_id: ids[i], operator_id: operatorId }),
            });
            const json = await resp.json();
            if (json.code === 0) ok++; else fail++;
        } catch (e) { fail++; }
        setFileMgrStatus(`节点${verb}中（${i + 1}/${ids.length}）…`);
    }
    setFileMgrStatus(`节点${verb}完成：成功 ${ok}，失败 ${fail}`);
    loadFileMgrFiles();
}

async function doFileMgrRagToggle(fileId, enter) {
    const operatorId = getGlobalOperator();
    if (!operatorId) {
        setFileMgrStatus('请选择操作人');
        loadFileMgrFiles();
        return;
    }
    const backend = selectedBackend('#file-mgr-backend');
    try {
        const url = enter ? '/rag-index' : '/rag-delete';
        const body = enter
            ? { file_id: fileId, user_id: operatorId, backend }
            : { doc_id: fileId, operator_id: operatorId };
        const resp = await fetch(API_CONSOLE + url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const json = await resp.json();
        setFileMgrStatus(json.code === 0
            ? (enter ? '已入库' : '已出库')
            : ((enter ? '入库失败：' : '出库失败：') + (json.message || '')));
    } catch (e) {
        setFileMgrStatus('操作失败：' + e.message);
    }
    loadFileMgrFiles();
}

q('#upload-btn').addEventListener('click', doUpload);
q('#file-mgr-check-all').addEventListener('change', e => {
    document.querySelectorAll('.file-mgr-check').forEach(cb => { cb.checked = e.target.checked; });
});
q('#file-mgr-tbody').addEventListener('change', async e => {
    const sel = e.target.closest('.file-mgr-conf-select');
    if (!sel) return;
    const fileId = sel.getAttribute('data-file');
    const confidentiality = parseInt(sel.value, 10);
    const operatorId = getGlobalOperator();
    if (!operatorId) {
        setFileMgrStatus('请先选择操作人（密级调整权限：上传人本人或 L5/L6）');
        loadFileMgrFiles();
        return;
    }
    setFileMgrStatus('密级调整中…');
    try {
        const resp = await fetch(API_CONSOLE + '/file-confidentiality', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: fileId, confidentiality, operator_id: operatorId }),
        });
        const json = await resp.json();
        setFileMgrStatus(json.code === 0
            ? `密级已更新：${confidentialityLabel(confidentiality)}`
            : `密级调整失败：${json.message || ''}`);
    } catch (err) {
        setFileMgrStatus('密级调整失败：' + err.message);
    }
    loadFileMgrFiles();
});
q('#file-mgr-delete-btn').addEventListener('click', doFileMgrDelete);
q('#file-mgr-node-add-btn').addEventListener('click', () => doFileMgrNode('add'));
q('#file-mgr-node-remove-btn').addEventListener('click', () => doFileMgrNode('remove'));
q('#file-mgr-tbody').addEventListener('change', e => {
    const cb = e.target;
    if (cb.classList && cb.classList.contains('file-mgr-rag-check')) {
        doFileMgrRagToggle(cb.value, cb.checked);
    }
});
document.querySelectorAll('.file-mgr-table th.sortable').forEach(th => {
    th.addEventListener('click', () => {
        const key = th.getAttribute('data-sort-key');
        if (_fileMgrSort.key === key) {
            _fileMgrSort.dir = -_fileMgrSort.dir;
        } else {
            _fileMgrSort = { key, dir: 1 };
        }
        renderFileMgrTable(_fileMgrRows);
    });
});

// ── RAG 模块（查库）──

let _ragDataLoaded = false;

// 选中左侧「RAG 模块」条目：右侧显示 RAG 面板，隐藏其他
function selectRag() {
    _selectedName = RAG_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(RAG_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#rag-panel').hidden = false;

    ensureRagData();
}

async function ensureRagData() {
    if (_ragDataLoaded) return;
    try {
        loadRagBackends();
        _ragDataLoaded = true;
    } catch (e) {
        // 数据加载失败不阻断面板
    }
}

async function loadRagBackends() {
    try {
        const resp = await fetch(API_CONSOLE + '/rag/backends');
        const json = await resp.json();
        if (json.code !== 0 || !json.data) return;
        applyBackendOptions(q('#rag-search-backend'), json.data, 'rag-search-backend');
    } catch (e) {
        // 探测失败保持默认（API 可选、本地灰）
    }
}

function applyBackendOptions(container, data, name) {
    const apiAvail = !!(data.api && data.api.available);
    const localAvail = !!(data.local && data.local.available);
    const apiLabel = (data.api && data.api.label) || '远程 API';
    const localLabel = (data.local && data.local.label) || '本地 TEI';
    const localReason = (data.local && data.local.reason) || '本地 TEI 不可用';

    // API 优先选中；API 不可用但本地可用时回退选本地
    const apiChecked = apiAvail || !localAvail ? ' checked' : '';
    const localChecked = !apiAvail && localAvail ? ' checked' : '';

    container.innerHTML =
        `<label class="${apiAvail ? '' : 'disabled'}" title="${escapeAttr(apiAvail ? '' : '远程 API 未配置')}"><input type="radio" name="${name}" value="api"${apiAvail ? '' : ' disabled'}${apiChecked}> ${escapeHtml(apiLabel)}</label>` +
        `<label class="${localAvail ? '' : 'disabled'}" title="${escapeAttr(localReason)}"><input type="radio" name="${name}" value="local"${localAvail ? '' : ' disabled'}${localChecked}> ${escapeHtml(localLabel)}</label>`;
}

function selectedBackend(containerSel) {
    const el = q(containerSel).querySelector('input[type="radio"]:checked');
    return el ? el.value : 'api';
}

async function doRagSearch() {
    const query = q('#rag-search-input').value.trim();
    const backend = selectedBackend('#rag-search-backend');
    const status = q('#rag-search-status');
    const btn = q('#rag-search-btn');
    const resultsBox = q('#rag-search-results');

    if (!query) {
        status.textContent = '请输入查询内容';
        return;
    }

    btn.disabled = true;
    status.textContent = '检索中…';
    try {
        const userId = getGlobalOperator();
        const resp = await fetch(API_CONSOLE + '/rag-search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, backend, top_k: 5, user_id: userId }),
        });
        const json = await resp.json();
        if (json.code === 0) {
            status.textContent = `命中 ${json.data.total} 条`;
            renderRagSearchResults(json.data.results);
        } else {
            status.textContent = `查库失败：${json.message || 'unknown'}`;
            resultsBox.hidden = true;
        }
    } catch (e) {
        status.textContent = `查库失败：${e.message}`;
        resultsBox.hidden = true;
    } finally {
        btn.disabled = false;
    }
}

function renderRagSearchResults(results) {
    const box = q('#rag-search-results');
    if (!results || !results.length) {
        box.hidden = true;
        return;
    }
    box.hidden = false;
    box.innerHTML = results.map((r, i) => `
        <div class="rag-search-item">
            <div class="rag-search-item-head">
                <span class="rag-search-score">#${i + 1} · score ${Number(r.score || 0).toFixed(3)}</span>
                <span class="rag-search-source">${escapeHtml(r.source_title || r.source_document || r.source_file_id || '')}</span>
            </div>
            <div class="rag-search-content">${escapeHtml(r.content)}</div>
        </div>`).join('');
}

q('#rag-search-btn').addEventListener('click', doRagSearch);

// ── 全景节点表 ──

let _nodeTableUsersLoaded = false;
let _nodeTableFilesLoaded = false;
let _nodeTableUsers = [];   // [{value,label,note}]
let _nodeTableFiles = [];   // [{id,name,file_no}]

const ROLE_LABEL = { participant: '参与人', approver: '审批人', observer: '观察者' };

// 全景节点三级类型（MILESTONE / WORK_PACKAGE / TASK）
const NODE_TYPE_LABEL = { MILESTONE: '里程碑', WORK_PACKAGE: '工作包', TASK: '任务' };
const NODE_TYPE_CLASS = { MILESTONE: 'nt-milestone', WORK_PACKAGE: 'nt-workpackage', TASK: 'nt-task' };

function nodeTypeHtml(t) {
    const v = String(t || '').trim();
    if (!v) return '<span class="muted">—</span>';
    const label = NODE_TYPE_LABEL[v] || v;
    return `<span class="node-type-badge ${NODE_TYPE_CLASS[v] || ''}">${escapeHtml(label)}</span>`;
}

// 选中左侧「全景节点表」条目：右侧显示节点大表，隐藏其他
function selectNodeTable() {
    _selectedName = NODE_TABLE_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(NODE_TABLE_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#node-table-panel').hidden = false;

    ensureNodeTableUsers();
    loadNodeTable(getGlobalOperator());
}

async function ensureNodeTableUsers() {
    if (_nodeTableUsersLoaded) return;
    await ensureGlobalUsers();
    _nodeTableUsers = _globalUsers;
    _nodeTableUsersLoaded = true;
}

async function ensureNodeTableFiles() {
    if (_nodeTableFilesLoaded) return;
    try {
        const resp = await fetch(API_CONSOLE + '/resources');
        const json = await resp.json();
        if (json.code === 0 && json.data) {
            _nodeTableFiles = (json.data.groups && json.data.groups.files) || [];
        }
        _nodeTableFilesLoaded = true;
    } catch (e) {
        // 文件列表加载失败不阻断节点表
    }
}

function setNodeTableStatus(msg, isError = false) {
    const el = q('#node-table-status');
    el.textContent = msg;
    el.style.color = isError ? '#ff6b6b' : '#4caf50';
    if (msg) {
        setTimeout(() => { if (el.textContent === msg) el.textContent = ''; }, 4000);
    }
}

function currentOperatorId() {
    return getGlobalOperator();
}

function requireOperator() {
    if (!currentOperatorId()) {
        setNodeTableStatus('请先选择操作人', true);
        return false;
    }
    return true;
}

async function loadNodeTable(userId) {
    const tbody = q('#node-table-tbody');
    const empty = q('#node-table-empty');
    tbody.innerHTML = '<tr><td colspan="6" class="hint">加载中…</td></tr>';
    empty.hidden = true;
    try {
        const url = userId
            ? `${API_CONSOLE}/node-table?user_id=${encodeURIComponent(userId)}`
            : `${API_CONSOLE}/node-table`;
        const resp = await fetch(url);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            tbody.innerHTML = `<tr><td colspan="6" class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</td></tr>`;
            return;
        }
        renderNodeTable(json.data.rows || []);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" class="hint">网络错误：${escapeHtml(e.message)}</td></tr>`;
    }
}

function renderNodeTable(rows) {
    const tbody = q('#node-table-tbody');
    const empty = q('#node-table-empty');
    if (!rows.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = rows.map(r => {
        const nodeAttr = escapeAttr(r.node_id);
        // 参与人
        const partLis = (r.participants || []).map(p => {
            const role = p.role && p.role !== 'participant'
                ? ` <span class="muted">${escapeHtml(ROLE_LABEL[p.role] || p.role)}</span>` : '';
            return `<li><span>${escapeHtml(p.name || p.user_id || '')}${role}</span>
                <button class="cell-del" data-action="del-participant" data-node="${nodeAttr}" data-user="${escapeAttr(p.user_id)}" title="移除参与人">×</button></li>`;
        }).join('');
        const participantsHtml = partLis
            ? `<ul class="cell-list">${partLis}</ul>` : '<span class="muted">—</span>';
        // 共享文件
        const fileLis = (r.shared_files || []).map(f => {
            const label = (f.file_no ? f.file_no + ' ' : '') + (f.filename || f.file_id || '');
            return `<li><span>${escapeHtml(label)}</span>
                <button class="cell-del" data-action="del-file" data-node="${nodeAttr}" data-file="${escapeAttr(f.file_id)}" title="移除共享文件">×</button></li>`;
        }).join('');
        const filesHtml = fileLis
            ? `<ul class="cell-list">${fileLis}</ul>` : '<span class="muted">—</span>';
        return `<tr>
            <td>${escapeHtml(r.node_id)}</td>
            <td>${escapeHtml(r.node_name || '')}</td>
            <td>${nodeTypeHtml(r.node_type)}</td>
            <td>${escapeHtml(r.status || '')}</td>
            <td>${participantsHtml}<button class="cell-add" data-action="add-participant" data-node="${nodeAttr}">＋ 参与人</button></td>
            <td>${filesHtml}<button class="cell-add" data-action="add-file" data-node="${nodeAttr}">＋ 文件</button></td>
        </tr>`;
    }).join('');
}

// ── 现有 SOP 展示 ──

function selectSopDisplay() {
    _selectedName = SOP_DISPLAY_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(SOP_DISPLAY_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#sop-display-panel').hidden = false;

    ensureGlobalUsers();
    loadSopDisplay(getGlobalOperator());
}

async function loadSopDisplay(userId) {
    const tbody = q('#sop-display-tbody');
    const empty = q('#sop-display-empty');
    tbody.innerHTML = '<tr><td colspan="7" class="hint">加载中…</td></tr>';
    empty.hidden = true;
    try {
        const url = userId
            ? `${API_CONSOLE}/sops?user_id=${encodeURIComponent(userId)}`
            : `${API_CONSOLE}/sops`;
        const resp = await fetch(url);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            tbody.innerHTML = `<tr><td colspan="7" class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</td></tr>`;
            return;
        }
        renderSopDisplay(json.data.rows || []);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" class="hint">网络错误：${escapeHtml(e.message)}</td></tr>`;
    }
}

function renderSopDisplay(rows) {
    const tbody = q('#sop-display-tbody');
    const empty = q('#sop-display-empty');
    if (!rows.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = rows.map(r => {
        const status = r.is_deprecated
            ? '<span class="muted">已废弃</span>'
            : (r.is_active ? '<span style="color:#4caf50">启用</span>' : '<span class="muted">停用</span>');
        return `<tr>
            <td>${escapeHtml(r.sop_id || '')}</td>
            <td>${escapeHtml(r.name || '')}</td>
            <td>${escapeHtml(r.file_name || '')}</td>
            <td>${escapeHtml(r.category || '')}</td>
            <td>${escapeHtml(r.sop_type || '')}</td>
            <td>${status}</td>
            <td>${escapeHtml(r.description || '')}</td>
        </tr>`;
    }).join('');
}

// ── 参考全景节点展示（只读）──

function selectPanoramaNodes() {
    _selectedName = PANORAMA_NODES_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(PANORAMA_NODES_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = false;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;

    ensureGlobalUsers();
    loadPanoramaNodes(getGlobalOperator());
}

async function loadPanoramaNodes(userId) {
    const tbody = q('#panorama-nodes-tbody');
    const empty = q('#panorama-nodes-empty');
    tbody.innerHTML = '<tr><td colspan="7" class="hint">加载中…</td></tr>';
    empty.hidden = true;
    try {
        const url = userId
            ? `${API_CONSOLE}/node-table?user_id=${encodeURIComponent(userId)}`
            : `${API_CONSOLE}/node-table`;
        const resp = await fetch(url);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            tbody.innerHTML = `<tr><td colspan="7" class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</td></tr>`;
            return;
        }
        renderPanoramaNodes(json.data.rows || []);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" class="hint">网络错误：${escapeHtml(e.message)}</td></tr>`;
    }
}

function renderPanoramaNodes(rows) {
    const tbody = q('#panorama-nodes-tbody');
    const empty = q('#panorama-nodes-empty');
    if (!rows.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = rows.map(r => {
        const participantNames = (r.participants || [])
            .map(p => escapeHtml(p.name || p.user_id || '')).join('、');
        const fileNames = (r.shared_files || [])
            .map(f => escapeHtml((f.file_no ? f.file_no + ' ' : '') + (f.filename || f.file_id || ''))).join('、');
        return `<tr>
            <td>${escapeHtml(r.node_id)}</td>
            <td>${escapeHtml(r.node_name || '')}</td>
            <td>${nodeTypeHtml(r.node_type)}</td>
            <td>${escapeHtml(r.status || '')}</td>
            <td>${escapeHtml(r.project_name || '')}</td>
            <td>${participantNames || '<span class="muted">—</span>'}</td>
            <td>${fileNames || '<span class="muted">—</span>'}</td>
        </tr>`;
    }).join('');
}

// ── 会话池（只读观测：活跃 Session + 最近消息）──

function selectSessionPool() {
    _selectedName = SESSION_POOL_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(SESSION_POOL_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#session-pool-panel').hidden = false;

    loadSessionPool();
}

function _fmtIdle(sec) {
    const s = Number(sec) || 0;
    if (s < 60) return `${s} 秒`;
    if (s < 3600) return `${Math.floor(s / 60)} 分`;
    return `${Math.floor(s / 3600)} 时 ${Math.floor((s % 3600) / 60)} 分`;
}

function _fmtTs(ts) {
    if (!ts) return '—';
    const d = new Date(Number(ts) * 1000);
    return isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false });
}

async function loadSessionPool() {
    const tbody = q('#session-pool-tbody');
    const empty = q('#session-pool-empty');
    const status = q('#session-pool-status');
    const msgBox = q('#session-pool-messages');
    tbody.innerHTML = '<tr><td colspan="4" class="hint">加载中…</td></tr>';
    empty.hidden = true;
    msgBox.hidden = true;
    try {
        const resp = await fetch(`${API_CONSOLE}/session-pool`);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            tbody.innerHTML = `<tr><td colspan="4" class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</td></tr>`;
            status.textContent = '';
            return;
        }
        const d = json.data;
        status.textContent = `活跃 ${d.total} 个 · 池运行 ${_fmtIdle(d.uptime_seconds)}`;
        renderSessionPool(d.sessions || []);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" class="hint">网络错误：${escapeHtml(e.message)}</td></tr>`;
    }
}

function renderSessionPool(sessions) {
    const tbody = q('#session-pool-tbody');
    const empty = q('#session-pool-empty');
    if (!sessions.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = sessions.map(s => {
        const cid = escapeAttr(s.conversation_id);
        return `<tr>
            <td class="cell-link" data-action="session-messages" data-conv="${cid}">${escapeHtml(s.conversation_id)}</td>
            <td>${_fmtIdle(s.idle_seconds)}</td>
            <td>${_fmtTs(s.last_active_ts)}</td>
            <td><button class="cell-add" data-action="session-messages" data-conv="${cid}">查看消息</button></td>
        </tr>`;
    }).join('');
    tbody.querySelectorAll('[data-action="session-messages"]').forEach(el => {
        el.addEventListener('click', () => showSessionMessages(el.dataset.conv));
    });
}

async function showSessionMessages(convId) {
    const box = q('#session-pool-messages');
    box.hidden = false;
    box.innerHTML = '<div class="hint">加载中…</div>';
    try {
        const resp = await fetch(`${API_CONSOLE}/session-pool/${encodeURIComponent(convId)}/messages?limit=5`);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            box.innerHTML = `<div class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</div>`;
            return;
        }
        const rows = json.data.messages || [];
        if (!rows.length) {
            box.innerHTML = `<div class="hint">会话 ${escapeHtml(convId)} 暂无消息</div>`;
            return;
        }
        const items = rows.map(m => {
            const dir = m.direction === 'agent_to_user' ? 'Emily' : (m.sender_name || '用户');
            const ts = String(m.created_at || '').slice(0, 19);
            return `<li><span class="muted">[${escapeHtml(ts)}]</span> `
                + `<b>${escapeHtml(dir)}</b>：${escapeHtml(m.content || '')}</li>`;
        }).join('');
        box.innerHTML = `<div class="session-pool-msg-head">会话 ${escapeHtml(convId)} · 最近 ${rows.length} 条</div>`
            + `<ul class="session-pool-msg-list">${items}</ul>`;
    } catch (e) {
        box.innerHTML = `<div class="hint">网络错误：${escapeHtml(e.message)}</div>`;
    }
}

// ── 会话归档（只读观测：归档索引 + 点姓名看对话全文）──

const ARCHIVE_REASON_LABEL = { expired: 'TTL 超时', terminated: '手动终止', manual: '手动归档' };

function _fmtBytes(n) {
    const b = Number(n) || 0;
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

function selectSessionArchive() {
    _selectedName = SESSION_ARCHIVE_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(SESSION_ARCHIVE_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = false;

    loadSessionArchive();
}

async function loadSessionArchive() {
    const tbody = q('#session-archive-tbody');
    const empty = q('#session-archive-empty');
    const status = q('#session-archive-status');
    const box = q('#session-archive-content');
    tbody.innerHTML = '<tr><td colspan="6" class="hint">加载中…</td></tr>';
    empty.hidden = true;
    box.hidden = true;
    try {
        const resp = await fetch(`${API_CONSOLE}/session-archives`);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            tbody.innerHTML = `<tr><td colspan="6" class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</td></tr>`;
            status.textContent = '';
            return;
        }
        const rows = json.data.rows || [];
        status.textContent = `共 ${rows.length} 条归档`;
        renderSessionArchive(rows);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" class="hint">网络错误：${escapeHtml(e.message)}</td></tr>`;
    }
}

let _archiveSort = { key: 'archived_at', dir: -1 }; // 默认归档时间倒序
let _archiveRows = [];

function archiveSortValue(row, key) {
    switch (key) {
        case 'archived_at': {
            const ts = Date.parse((row.archived_at || '').replace(' ', 'T'));
            return Number.isFinite(ts) ? ts : 0;
        }
        case 'turn_count':
            return Number(row.turn_count) || 0;
        case 'user_name':
            return (row.user_name || '').toLowerCase();
        case 'conversation_id':
            return (row.conversation_id || '').toLowerCase();
        default:
            return '';
    }
}

function archiveCompare(a, b) {
    const { key, dir } = _archiveSort;
    if (!key) return 0;
    const va = archiveSortValue(a, key);
    const vb = archiveSortValue(b, key);
    let cmp;
    if (typeof va === 'number' && typeof vb === 'number') {
        cmp = va - vb;
    } else {
        cmp = String(va).localeCompare(String(vb), 'zh-CN');
    }
    return cmp * dir;
}

function updateArchiveSortIndicators() {
    document.querySelectorAll('#session-archive-panel th.sortable').forEach(th => {
        const ind = th.querySelector('.sort-ind');
        if (!ind) return;
        ind.textContent = (th.getAttribute('data-sort-key') === _archiveSort.key)
            ? (_archiveSort.dir === 1 ? ' ▲' : ' ▼')
            : '';
    });
}

function renderSessionArchive(rows) {
    _archiveRows = rows || [];
    const tbody = q('#session-archive-tbody');
    const empty = q('#session-archive-empty');
    const list = _archiveRows.slice();
    if (_archiveSort.key) {
        list.sort(archiveCompare);
    }
    updateArchiveSortIndicators();
    if (!list.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = list.map(r => {
        const id = escapeAttr(r.id);
        const name = escapeHtml(r.user_name || '(未知)');
        const convId = escapeHtml(r.conversation_id || '');
        const convHtml = r.has_content
            ? `<span class="cell-link" data-archive="${id}">${convId}</span>`
            : `${convId} <span class="muted">(无正文)</span>`;
        const reason = ARCHIVE_REASON_LABEL[r.archive_reason] || escapeHtml(r.archive_reason || '');
        const fname = String(r.md_file_path || '').split('/').pop();
        const size = r.has_content ? _fmtBytes(r.file_size) : '—';
        return `<tr>
            <td>${escapeHtml(String(r.archived_at || '').replace('T', ' ').slice(0, 19))}</td>
            <td>${name}</td>
            <td>${convHtml}</td>
            <td>${r.turn_count || 0}</td>
            <td>${reason}</td>
            <td>${escapeHtml(fname)} <span class="muted">${size}</span></td>
        </tr>`;
    }).join('');
    tbody.querySelectorAll('[data-archive]').forEach(el => {
        el.addEventListener('click', () => showSessionArchiveContent(el.dataset.archive));
    });
}

async function showSessionArchiveContent(archiveId) {
    const box = q('#session-archive-content');
    box.hidden = false;
    box.innerHTML = '<div class="hint">加载中…</div>';
    try {
        const resp = await fetch(`${API_CONSOLE}/session-archives/${encodeURIComponent(archiveId)}/content`);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            box.innerHTML = `<div class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</div>`;
            return;
        }
        const d = json.data;
        const head = `会话 ${escapeHtml(d.conversation_id || '')} · ${escapeHtml(d.user_name || '')}`
            + ` · ${escapeHtml(String(d.archived_at || '').replace('T', ' ').slice(0, 19))}`
            + ` · ${d.turn_count || 0} 轮 · ${escapeHtml(d.file_name || '')}`;
        box.innerHTML = `<div class="session-archive-head">${head}</div>`
            + `<pre class="session-archive-pre">${escapeHtml(d.content || '')}</pre>`;
    } catch (e) {
        box.innerHTML = `<div class="hint">网络错误：${escapeHtml(e.message)}</div>`;
    }
}

// 会话归档表头排序：点击切换 升序 / 降序
document.querySelectorAll('#session-archive-panel th.sortable').forEach(th => {
    th.addEventListener('click', () => {
        const key = th.getAttribute('data-sort-key');
        if (_archiveSort.key === key) {
            _archiveSort.dir = -_archiveSort.dir;
        } else {
            _archiveSort = { key, dir: 1 };
        }
        renderSessionArchive(_archiveRows);
    });
});

// ── 测试用例库（只读）：回归用例清单 + 单条用例细节 ──

function selectTestCases() {
    _selectedName = TEST_CASES_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(TEST_CASES_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#test-cases-panel').hidden = false;

    loadTestCases();
}

async function loadTestCases() {
    const tbody = q('#test-cases-tbody');
    const empty = q('#test-cases-empty');
    const status = q('#test-cases-status');
    const box = q('#test-case-detail');
    tbody.innerHTML = '<tr><td colspan="4" class="hint">加载中…</td></tr>';
    empty.hidden = true;
    box.hidden = true;
    try {
        const resp = await fetch(`${API_CONSOLE}/test-cases`);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            tbody.innerHTML = `<tr><td colspan="4" class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</td></tr>`;
            status.textContent = '';
            return;
        }
        const rows = json.data.rows || [];
        const fileCount = (json.data.files || []).length;
        status.textContent = `${fileCount} 个类别文件 · 共 ${json.data.count ?? rows.length} 条用例`;
        renderTestCases(rows);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" class="hint">网络错误：${escapeHtml(e.message)}</td></tr>`;
    }
}

function renderTestCases(rows) {
    const tbody = q('#test-cases-tbody');
    const empty = q('#test-cases-empty');
    if (!rows.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = rows.map(r => {
        const noCell = `<span class="cell-link" data-case="${escapeAttr(r.id)}">${escapeHtml(r.case_no || '')}</span>`;
        return `<tr>
            <td>${noCell}</td>
            <td>${escapeHtml(r.title || '')}</td>
            <td>${escapeHtml(r.file || '')}</td>
            <td>${escapeHtml(r.section || '')}</td>
        </tr>`;
    }).join('');
    tbody.querySelectorAll('[data-case]').forEach(el => {
        el.addEventListener('click', () => showTestCaseDetail(el.dataset.case));
    });
}

async function showTestCaseDetail(caseId) {
    const box = q('#test-case-detail');
    box.hidden = false;
    box.innerHTML = '<div class="hint">加载中…</div>';
    try {
        const resp = await fetch(`${API_CONSOLE}/test-cases/${encodeURIComponent(caseId)}`);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            box.innerHTML = `<div class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</div>`;
            return;
        }
        const d = json.data;
        const head = `${escapeHtml(d.case_no || '')} · ${escapeHtml(d.file || '')}`
            + (d.section ? ` · ${escapeHtml(d.section)}` : '');
        const fieldsHtml = (d.fields || []).map(f =>
            `<div class="pe-detail-row"><span class="pe-detail-k">${escapeHtml(f.label)}</span><span class="pe-detail-v">${escapeHtml(f.value)}</span></div>`
        ).join('');
        const overviewHtml = d.overview
            ? `<div class="pe-detail-sec">类别说明</div><pre class="session-archive-pre">${escapeHtml(d.overview)}</pre>`
            : '';
        const preHtml = d.precondition
            ? `<div class="pe-detail-sec">前置</div><pre class="session-archive-pre">${escapeHtml(d.precondition)}</pre>`
            : '';
        const cmdHtml = d.command
            ? `<div class="pe-detail-sec">执行命令参考</div><pre class="session-archive-pre">${escapeHtml(d.command)}</pre>`
            : '';
        box.innerHTML = `<div class="session-archive-head">${head}</div>`
            + `<div class="pe-detail-grid">${fieldsHtml}</div>`
            + overviewHtml + preHtml + cmdHtml;
    } catch (e) {
        box.innerHTML = `<div class="hint">网络错误：${escapeHtml(e.message)}</div>`;
    }
}

// ── 项目事件（统一时间线：会议/任务/文件/流转/成果/节点事件）──

let _projectEventKindsLoaded = false;

// 选中左侧「项目事件」条目：右侧显示统一事件时间线，隐藏其他
function selectProjectEvents() {
    _selectedName = PROJECT_EVENTS_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(PROJECT_EVENTS_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#project-events-panel').hidden = false;

    loadProjectEvents();
}

async function loadProjectEvents() {
    const tbody = q('#project-events-tbody');
    const empty = q('#project-events-empty');
    const count = q('#project-events-count');
    tbody.innerHTML = '<tr><td colspan="7" class="hint">加载中…</td></tr>';
    empty.hidden = true;
    try {
        const params = new URLSearchParams();
        const kind = q('#project-events-kind-select').value;
        if (kind) params.set('kind', kind);
        params.set('limit', '300');
        const qs = params.toString();
        const resp = await fetch(API_CONSOLE + '/project-events' + (qs ? '?' + qs : ''));
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            tbody.innerHTML = `<tr><td colspan="7" class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</td></tr>`;
            return;
        }
        if (!_projectEventKindsLoaded) {
            const kinds = json.data.kinds || [];
            const display = json.data.kind_display || {};
            q('#project-events-kind-select').innerHTML = '<option value="">全部类型</option>'
                + kinds.map(k => `<option value="${escapeAttr(k)}">${escapeHtml(display[k] || k)}</option>`).join('');
            _projectEventKindsLoaded = true;
        }
        count.textContent = `共 ${json.data.count ?? (json.data.rows || []).length} 条`;
        renderProjectEvents(json.data.rows || []);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" class="hint">网络错误：${escapeHtml(e.message)}</td></tr>`;
    }
}

function fmtEventTime(t) {
    if (!t) return '';
    const s = String(t);
    const d = new Date(s);
    if (isNaN(d.getTime())) return escapeHtml(s.slice(0, 19).replace('T', ' '));
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function renderProjectEvents(rows) {
    const tbody = q('#project-events-tbody');
    const empty = q('#project-events-empty');
    if (!rows.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = rows.map(r => {
        const nodeLabel = r.node_name || r.node_id || '';
        const nodeCell = nodeLabel
            ? `<span title="${escapeAttr(r.node_id || '')}">${escapeHtml(nodeLabel)}</span>`
            : '<span class="muted">—</span>';
        const projectCell = r.project_name ? escapeHtml(r.project_name) : '<span class="muted">—</span>';
        const titleCell = r.id
            ? `<span class="cell-link" data-event="${escapeAttr(r.id)}">${escapeHtml(r.title || '')}</span>`
            : escapeHtml(r.title || '');
        return `<tr>
            <td class="log-time">${escapeHtml(r.event_no || '')}</td>
            <td>${escapeHtml(r.event_kind_display || r.event_kind || '')}</td>
            <td>${titleCell}</td>
            <td>${escapeHtml(r.status || '')}</td>
            <td>${nodeCell}</td>
            <td>${projectCell}</td>
            <td class="log-time">${fmtEventTime(r.created_at)}</td>
        </tr>`;
    }).join('');
    tbody.querySelectorAll('[data-event]').forEach(el => {
        el.addEventListener('click', () => showProjectEventDetail(el.dataset.event));
    });
}

async function showProjectEventDetail(eventId) {
    const box = q('#project-event-detail');
    box.hidden = false;
    box.innerHTML = '<div class="hint">加载中…</div>';
    try {
        const resp = await fetch(`${API_CONSOLE}/project-events/${encodeURIComponent(eventId)}`);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            box.innerHTML = `<div class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</div>`;
            return;
        }
        const d = json.data;
        const fieldRows = [
            ['编号', d.event_no],
            ['类型', d.event_kind_display || d.event_kind],
            ['子类型', d.event_type],
            ['状态', d.status],
            ['项目', d.project_name],
            ['节点', d.node_name || d.node_id],
            ['操作人', d.actor_name || d.actor_id],
            ['业务发生时间', fmtEventTime(d.occurred_at)],
            ['记录创建时间', fmtEventTime(d.created_at)],
            ['认证人', d.confirmed_by],
            ['认证时间', d.confirmed_at],
            ['来源消息', d.source_message_id],
        ].filter(([, v]) => v != null && String(v) !== '')
            .map(([k, v]) => `<div class="pe-detail-row"><span class="pe-detail-k">${escapeHtml(k)}</span><span class="pe-detail-v">${escapeHtml(String(v))}</span></div>`)
            .join('');
        const payload = (d.payload && typeof d.payload === 'object' && Object.keys(d.payload).length)
            ? JSON.stringify(d.payload, null, 2)
            : '';
        const head = `${escapeHtml(d.title || '(无标题)')} · ${escapeHtml(d.event_kind_display || d.event_kind || '')}`;
        const summaryHtml = d.summary
            ? `<div class="pe-detail-sec">摘要</div><pre class="session-archive-pre">${escapeHtml(d.summary)}</pre>`
            : '';
        const payloadHtml = payload
            ? `<div class="pe-detail-sec">扩展字段（payload）</div><pre class="session-archive-pre">${escapeHtml(payload)}</pre>`
            : '';
        box.innerHTML = `<div class="session-archive-head">${head}</div>`
            + `<div class="pe-detail-grid">${fieldRows}</div>`
            + summaryHtml
            + payloadHtml;
    } catch (e) {
        box.innerHTML = `<div class="hint">网络错误：${escapeHtml(e.message)}</div>`;
    }
}

q('#project-events-kind-select').addEventListener('change', () => loadProjectEvents());

function collectExistingIds(cell, dataKey) {
    const ids = [];
    cell.querySelectorAll(`[data-${dataKey}]`).forEach(el => {
        if (el.dataset[dataKey]) ids.push(el.dataset[dataKey]);
    });
    return ids;
}

function participantFormHtml(nodeId, existingIds) {
    const opts = _nodeTableUsers
        .filter(u => !existingIds.includes(u.value))
        .map(u => `<option value="${escapeAttr(u.value)}">${escapeHtml(u.label)}</option>`).join('');
    if (!opts) {
        return `<div class="cell-edit" data-form="participant"><span class="muted">无候选用户</span><button class="mini-btn cancel" data-cancel>取消</button></div>`;
    }
    return `<div class="cell-edit" data-form="participant">
        <select class="edit-user">${opts}</select>
        <select class="edit-role">
            <option value="participant">参与人</option>
            <option value="approver">审批人</option>
            <option value="observer">观察者</option>
        </select>
        <button class="mini-btn ok" data-ok="participant" data-node="${escapeAttr(nodeId)}">确定</button>
        <button class="mini-btn cancel" data-cancel>取消</button>
    </div>`;
}

function fileFormHtml(nodeId, existingIds) {
    const opts = _nodeTableFiles
        .filter(f => !existingIds.includes(f.id))
        .map(f => `<option value="${escapeAttr(f.id)}">${escapeHtml((f.file_no ? f.file_no + ' ' : '') + (f.name || f.id))}</option>`).join('');
    if (!opts) {
        return `<div class="cell-edit" data-form="file"><span class="muted">无候选文件</span><button class="mini-btn cancel" data-cancel>取消</button></div>`;
    }
    return `<div class="cell-edit" data-form="file">
        <select class="edit-file">${opts}</select>
        <button class="mini-btn ok" data-ok="file" data-node="${escapeAttr(nodeId)}">确定</button>
        <button class="mini-btn cancel" data-cancel>取消</button>
    </div>`;
}

async function toggleAddForm(btn, kind, nodeId) {
    const cell = btn.parentElement;
    if (kind === 'participant') await ensureNodeTableUsers();
    else await ensureNodeTableFiles();
    if (cell.querySelector('[data-form]')) return;
    const existingIds = collectExistingIds(cell, kind === 'participant' ? 'user' : 'file');
    btn.insertAdjacentHTML('beforebegin', kind === 'participant'
        ? participantFormHtml(nodeId, existingIds)
        : fileFormHtml(nodeId, existingIds));
    btn.hidden = true;
}

async function mutateNodeParticipant(action, nodeId, userId, role = '') {
    try {
        const resp = await fetch(API_CONSOLE + '/node-participant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, node_id: nodeId, user_id: userId, operator_id: currentOperatorId(), role }),
        });
        const json = await resp.json();
        if (json.code !== 0) {
            setNodeTableStatus(json.message || '操作失败', true);
            return;
        }
        setNodeTableStatus('操作成功');
        await loadNodeTable(getGlobalOperator());
    } catch (e) {
        setNodeTableStatus('网络错误：' + e.message, true);
    }
}

async function mutateNodeFile(action, nodeId, fileId) {
    try {
        const resp = await fetch(API_CONSOLE + '/node-file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, node_id: nodeId, file_id: fileId, operator_id: currentOperatorId() }),
        });
        const json = await resp.json();
        if (json.code !== 0) {
            setNodeTableStatus(json.message || '操作失败', true);
            return;
        }
        setNodeTableStatus('操作成功');
        await loadNodeTable(getGlobalOperator());
    } catch (e) {
        setNodeTableStatus('网络错误：' + e.message, true);
    }
}

// 增删操作事件委托（tbody 为静态容器）
q('#node-table-tbody').addEventListener('click', async ev => {
    const okBtn = ev.target.closest('[data-ok]');
    if (okBtn) {
        if (!requireOperator()) return;
        const form = okBtn.closest('[data-form]');
        const nodeId = okBtn.dataset.node;
        if (okBtn.dataset.ok === 'participant') {
            const sel = form.querySelector('.edit-user');
            if (!sel || !sel.value) { setNodeTableStatus('请选择要添加的参与人', true); return; }
            await mutateNodeParticipant('add', nodeId, sel.value, form.querySelector('.edit-role').value);
        } else if (okBtn.dataset.ok === 'file') {
            const sel = form.querySelector('.edit-file');
            if (!sel || !sel.value) { setNodeTableStatus('请选择要添加的文件', true); return; }
            await mutateNodeFile('add', nodeId, sel.value);
        }
        return;
    }
    const cancelBtn = ev.target.closest('[data-cancel]');
    if (cancelBtn) {
        const form = cancelBtn.closest('[data-form]');
        const cell = form.parentElement;
        form.remove();
        const addBtn = cell.querySelector('.cell-add');
        if (addBtn) addBtn.hidden = false;
        return;
    }
    const btn = ev.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const nodeId = btn.dataset.node;
    if (action === 'del-participant') {
        if (!requireOperator()) return;
        if (!confirm('确认移除该参与人？')) return;
        await mutateNodeParticipant('remove', nodeId, btn.dataset.user, '');
    } else if (action === 'del-file') {
        if (!requireOperator()) return;
        if (!confirm('确认移除该共享文件？')) return;
        await mutateNodeFile('remove', nodeId, btn.dataset.file);
    } else if (action === 'add-participant') {
        toggleAddForm(btn, 'participant', nodeId);
    } else if (action === 'add-file') {
        toggleAddForm(btn, 'file', nodeId);
    }
});

// ── 日志聚合 ──

let _logsUsersLoaded = false;
let _logsModulesLoaded = false;
let _logsUsers = [];    // [{value,label,note}]
let _logsUserMap = {};  // value -> label（用于日志行显示用户名）

// 选中左侧「日志聚合」条目：右侧显示日志表，隐藏其他
function selectLogs() {
    _selectedName = LOG_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(LOG_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#logs-panel').hidden = false;

    ensureLogsUsers();
    loadLogs(q('#logs-module-select').value, getGlobalOperator());
    renderSimulatorForm();
}

async function ensureLogsUsers() {
    if (_logsUsersLoaded) return;
    await ensureGlobalUsers();
    _logsUsers = _globalUsers;
    _logsUserMap = {};
    _logsUsers.forEach(u => { _logsUserMap[u.value] = u.label; });
    _logsUsersLoaded = true;
}

async function loadLogs(module, userId) {
    const tbody = q('#logs-tbody');
    const empty = q('#logs-empty');
    tbody.innerHTML = '<tr><td colspan="4" class="hint">加载中…</td></tr>';
    empty.hidden = true;
    try {
        const params = new URLSearchParams();
        if (module) params.set('module', module);
        if (userId) params.set('user_id', userId);
        params.set('limit', '500');
        const qs = params.toString();
        const resp = await fetch(API_CONSOLE + '/logs' + (qs ? '?' + qs : ''));
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            tbody.innerHTML = `<tr><td colspan="4" class="hint">加载失败：${escapeHtml(json.message || 'unknown')}</td></tr>`;
            return;
        }
        if (!_logsModulesLoaded) {
            const mods = json.data.modules || [];
            q('#logs-module-select').innerHTML = '<option value="">全部模块</option>'
                + mods.map(m => `<option value="${escapeAttr(m.key)}">${escapeHtml(m.label)}</option>`).join('');
            _logsModulesLoaded = true;
        }
        renderLogs(json.data.rows || []);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" class="hint">网络错误：${escapeHtml(e.message)}</td></tr>`;
    }
}

function fmtLogTime(t) {
    if (!t) return '';
    const d = new Date(t);
    if (isNaN(d.getTime())) {
        return escapeHtml(String(t).slice(0, 19).replace('T', ' '));
    }
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function renderLogs(rows) {
    const tbody = q('#logs-tbody');
    const empty = q('#logs-empty');
    if (!rows.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = rows.map(r => {
        const userName = _logsUserMap[r.user_id] || (r.user_id ? r.user_id.slice(0, 8) : '');
        return `<tr>
            <td class="log-time">${fmtLogTime(r.time)}</td>
            <td class="log-module">${escapeHtml(r.module_name || r.module)}</td>
            <td class="log-user">${userName ? escapeHtml(userName) : '<span class="muted">—</span>'}</td>
            <td class="log-summary">${escapeHtml(r.summary || '')}</td>
        </tr>`;
    }).join('');
}

q('#logs-module-select').addEventListener('change', ev => loadLogs(ev.target.value, getGlobalOperator()));

// ── 消息模拟器（日志聚合框架内）──
// 发送者不再单独提供下拉，统一使用左侧全局「操作人」。

let _simulatorSchema = null;
let _simulatorRendered = false;
let _pendingSimulatorValues = null;

async function renderSimulatorForm() {
    if (_simulatorRendered) return;
    _simulatorRendered = true;
    const form = q('#simulator-form');
    form.innerHTML = '<div class="hint" style="padding:12px">加载参数…</div>';
    try {
        const resp = await fetch(`${API}/schema/${encodeURIComponent(MESSAGE_SIMULATOR_NAME)}`);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            form.innerHTML = `<div class="hint" style="padding:12px">加载失败：${escapeHtml(json.message || 'unknown')}</div>`;
            return;
        }
        _simulatorSchema = json.data;
        const params = simulatorParams();
        form.innerHTML = params.map(p => renderParamGroup(p)).join('');
    } catch (e) {
        form.innerHTML = `<div class="hint" style="padding:12px">网络错误：${escapeHtml(e.message)}</div>`;
    }
}

function simulatorParams() {
    return ((_simulatorSchema && _simulatorSchema.params) || []).filter(p => p.name !== 'sender');
}

function collectSimulatorValues() {
    const form = q('#simulator-form');
    const values = {};
    simulatorParams().forEach(p => {
        if (p.type === 'flag') {
            const el = form.querySelector(`#param-${p.name}`);
            if (el && el.checked) values[p.name] = true;
        } else if (p.type === 'enum' && p.choices && p.choices.length > 0 && p.choices.length <= 6) {
            const sel = form.querySelector(`input[name="radio-${p.name}"]:checked`);
            if (sel && sel.value !== '') values[p.name] = sel.value;
        } else {
            const el = form.querySelector(`#param-${p.name}`);
            if (!el) return;
            if (p.type === 'int') {
                if (el.value !== '') values[p.name] = parseInt(el.value, 10);
            } else if (el.value !== '') {
                values[p.name] = el.value;
            }
        }
    });
    return values;
}

function simulatorCliPreview(values) {
    const parts = [];
    simulatorParams().forEach(p => {
        const v = values[p.name];
        if (v == null) return;
        if (p.type === 'flag') { parts.push('--' + p.name); return; }
        parts.push('--' + p.name, String(v));
    });
    parts.push('--sender', getGlobalOperator() || '');
    return `uv run python scripts/${MESSAGE_SIMULATOR_NAME}.py ${parts.join(' ')}`.trim();
}

q('#simulator-run-btn').addEventListener('click', () => {
    const operator = getGlobalOperator();
    if (!operator) {
        q('#simulator-status').textContent = '请先选择操作人';
        return;
    }
    const values = collectSimulatorValues();
    values.sender = operator;
    _pendingSimulatorValues = values;
    q('#confirm-name').textContent = MESSAGE_SIMULATOR_NAME;
    q('#confirm-cmd').textContent = simulatorCliPreview(values);
    q('#confirm-overlay').classList.add('active');
});

async function runSimulator(values, confirmWrite) {
    const status = q('#simulator-status');
    const btn = q('#simulator-run-btn');
    const meta = q('#simulator-meta');
    const resultEl = q('#simulator-result');
    status.textContent = '发送中…';
    btn.disabled = true;
    meta.textContent = '';
    resultEl.className = 'result-body';
    resultEl.textContent = '';

    try {
        const resp = await fetch(`${API}/run/${encodeURIComponent(MESSAGE_SIMULATOR_NAME)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ values: values, confirm_write: confirmWrite, subcommand: null }),
        });
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            resultEl.textContent = 'API 错误: ' + (json.message || 'unknown');
            resultEl.className = 'result-body error';
            return;
        }
        const data = json.data;
        const parts = [];
        if (data.forced_preview) parts.push('⚠ 预览模式（未确认执行）');
        if (data.returncode != null) parts.push(`退出码: ${data.returncode}`);
        if (data.success) parts.push('✅ 成功');
        else if (data.returncode != null) parts.push('❌ 失败');
        if (data.cli_args) parts.push(`argv: ${data.cli_args.join(' ')}`);
        meta.textContent = parts.join(' | ');

        const output = [];
        if (data.stdout) output.push(data.stdout);
        if (data.stderr) output.push('── STDERR ──\n' + data.stderr);
        if (!data.stdout && !data.stderr) output.push(data.error || '(无输出)');
        resultEl.textContent = output.join('\n');

        if (data.forced_preview) resultEl.className = 'result-body preview';
        else if (data.success) resultEl.className = 'result-body success';
        else if (data.returncode != null) resultEl.className = 'result-body error';
        else resultEl.className = 'result-body';
    } catch (e) {
        resultEl.textContent = '网络错误: ' + e.message;
        resultEl.className = 'result-body error';
    } finally {
        btn.disabled = false;
        status.textContent = '';
    }
}

// ── 系统自检 ──

// 选中左侧「系统自检」条目：右侧显示自检面板，隐藏其他
function selectSelfCheck() {
    _selectedName = SELF_CHECK_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(SELF_CHECK_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#self-check-panel').hidden = false;
    q('#prompt-panel').hidden = true;

    ensureGlobalUsers();
}

// ── Prompt 工程 ──

function selectPrompt() {
    _selectedName = PROMPT_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(PROMPT_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = false;

    loadPrompts();
}

async function loadPrompts() {
    try {
        const resp = await fetch(API_CONSOLE + '/prompts');
        const json = await resp.json();
        if (json.code === 0 && json.data) {
            renderPromptList(json.data.prompts || []);
        }
    } catch (e) {
        q('#prompt-list').innerHTML = '<div class="hint">加载失败</div>';
    }
}

function renderPromptList(prompts) {
    const el = q('#prompt-list');
    q('#prompt-count').textContent = prompts.length;
    if (!prompts.length) {
        el.innerHTML = '<div class="hint">暂无模板</div>';
        return;
    }
    el.innerHTML = prompts.map(p =>
        `<div class="prompt-item" data-name="${escapeAttr(p.name)}">
            <span class="prompt-item-name">${escapeHtml(p.name)}</span>
            <span class="prompt-item-size">${formatSize(p.size || 0)}</span>
        </div>`
    ).join('');

    el.querySelectorAll('.prompt-item').forEach(item => {
        item.addEventListener('click', () => {
            el.querySelectorAll('.prompt-item').forEach(x => x.classList.remove('active'));
            item.classList.add('active');
            loadPromptDetail(item.dataset.name);
        });
    });
}

async function loadPromptDetail(name) {
    const metaName = q('#prompt-detail-name');
    const metaSource = q('#prompt-detail-source');
    const descEl = q('#prompt-detail-desc');
    const insertsEl = q('#prompt-detail-inserts');
    const contentEl = q('#prompt-detail-content');

    metaName.textContent = name;
    metaSource.textContent = '';
    descEl.textContent = '';
    insertsEl.innerHTML = '';
    contentEl.textContent = '加载中…';

    try {
        const resp = await fetch(API_CONSOLE + '/prompt?name=' + encodeURIComponent(name));
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            contentEl.textContent = '加载失败：' + (json.message || '');
            return;
        }
        const d = json.data;
        metaName.textContent = d.name || name;
        metaSource.textContent = d.source || '';
        descEl.textContent = d.description || '（无来源描述）';

        if ((d.insert_points || []).length) {
            insertsEl.innerHTML = d.insert_points.map(v =>
                `<code class="insert-tag">${escapeHtml('{' + v + '}')}</code>`
            ).join('');
        } else {
            insertsEl.innerHTML = '<span class="muted">（无插入点）</span>';
        }

        contentEl.textContent = d.content || '';
    } catch (e) {
        contentEl.textContent = '加载失败：' + e.message;
    }
}

async function runSelfCheck() {
    const userId = getGlobalOperator();
    const mode = q('#self-check-mode-select').value;
    const resultEl = q('#self-check-result');
    const btn = q('#self-check-run-btn');

    if (!userId) {
        resultEl.innerHTML = '<div class="hint">请先选择操作人（用于日志登记）</div>';
        return;
    }

    btn.disabled = true;
    resultEl.innerHTML = '<div class="hint">自检执行中…</div>';
    try {
        const resp = await fetch(API_CONSOLE + '/self-check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mode: mode,
                check_tool_registry: true,
                operator_id: userId,
            }),
        });
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            resultEl.innerHTML = `<div class="hint">自检失败：${escapeHtml(json.message || 'unknown')}</div>`;
            return;
        }
        renderSelfCheck(json.data);
    } catch (e) {
        resultEl.innerHTML = `<div class="hint">网络错误：${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }
}

function renderSelfCheck(report) {
    const stats = report.stats || {};
    const tc = report.tools_consistency || {};
    const tcSummary = tc.summary || null;
    const tcIssues = Array.isArray(tc.issues) ? tc.issues : [];
    const tcFatal = tc.fatal ?? (tcSummary ? tcSummary.fatal_issues : 0);
    const tcOk = tc.ok ?? (tcFatal === 0);

    const cards = [
        ['用户', `${stats.users?.active ?? 0} 活跃 / ${stats.users?.total ?? 0} 总计 / ${stats.users?.admins ?? 0} 管理员`],
        ['项目', `${stats.projects?.active ?? 0} 活跃 / ${stats.projects?.total ?? 0} 总计`],
        ['业务', `${stats.business?.events ?? 0} 事件 / ${stats.business?.tasks ?? 0} 任务 / ${stats.business?.nodes ?? 0} 节点`],
        ['世界书', `${stats.world_books?.total ?? 0} 份 / ${stats.world_books?.activated ?? 0} 已激活`],
        ['知识库', `${stats.knowledge?.sop_count ?? 0} 个 SOP`],
    ];

    const cardsHtml = cards.map(([label, value]) =>
        `<div class="sc-card"><div class="sc-card-label">${label}</div><div class="sc-card-value">${escapeHtml(value)}</div></div>`
    ).join('');

    const tcHtml = tcSummary
        ? `<div class="sc-note">已注册工具 ${tcSummary.registered} · 有 schema ${tcSummary.with_schema} · 问题 ${tcSummary.total_issues} · fatal ${tcSummary.fatal_issues}</div>`
        : `<div class="sc-note">问题 ${tc.issues ?? 0} 处 · fatal ${tcFatal}</div>`;

    const issuesHtml = tcIssues.length
        ? `<table class="node-table sc-issues-table"><thead><tr><th>严重度</th><th>检查项</th><th>工具</th><th>说明</th></tr></thead><tbody>`
            + tcIssues.map(i => `<tr>
                <td class="sc-sev">${escapeHtml(i.severity || '')}</td>
                <td>${escapeHtml(i.check || '')}</td>
                <td>${escapeHtml(i.tool || '')}</td>
                <td>${escapeHtml(i.detail || '')}</td>
            </tr>`).join('')
            + `</tbody></table>`
        : '<div class="sc-note sc-ok">工具一致性检查无问题</div>';

    const statusBadge = tcOk
        ? '<span class="sc-badge sc-badge-ok">一致</span>'
        : `<span class="sc-badge sc-badge-bad">${tcFatal} 处 fatal</span>`;

    q('#self-check-result').innerHTML = `
        <div class="sc-meta">检查时间 ${escapeHtml((report.checked_at || '').slice(0, 19).replace('T', ' '))} · 模式 ${escapeHtml(report.mode === 'quick' ? '快速' : '全量')} ${statusBadge}</div>
        <div class="sc-cards">${cardsHtml}</div>
        <div class="sc-section-title">工具一致性</div>
        ${tcHtml}
        ${issuesHtml}
    `;
}

q('#self-check-run-btn').addEventListener('click', runSelfCheck);


async function loadResources(userId) {
    RESOURCE_GROUPS.forEach(g => {
        const list = q('#' + GROUP_CONF[g].listId);
        if (list) list.innerHTML = '<div class="hint">加载中…</div>';
    });
    try {
        const url = userId
            ? `${API_CONSOLE}/resources?user_id=${encodeURIComponent(userId)}`
            : `${API_CONSOLE}/resources`;
        const resp = await fetch(url);
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            RESOURCE_GROUPS.forEach(g => {
                const list = q('#' + GROUP_CONF[g].listId);
                if (list) list.innerHTML = `<div class="resource-empty">加载失败：${escapeHtml(json.message || 'unknown')}</div>`;
            });
            return;
        }
        _resources = json.data.groups || {};
        renderResources();
    } catch (e) {
        RESOURCE_GROUPS.forEach(g => {
            const list = q('#' + GROUP_CONF[g].listId);
            if (list) list.innerHTML = `<div class="resource-empty">网络错误：${escapeHtml(e.message)}</div>`;
        });
    }
}

function renderResources() {
    RESOURCE_GROUPS.forEach(g => {
        const list = q('#' + GROUP_CONF[g].listId);
        const countEl = q('#' + GROUP_CONF[g].countId);
        if (!list) return;
        const items = _resources[g] || [];
        if (countEl) countEl.textContent = `${items.length} 项`;
        if (!items.length) {
            list.innerHTML = '<div class="resource-empty">（无可见项）</div>';
            return;
        }
        list.innerHTML = items.map(item => renderResourceItem(g, item)).join('');
    });
}

function renderResourceItem(group, item) {
    let name = '';
    let meta = '';
    switch (group) {
        case 'files':
            name = item.name || '';
            meta = [item.file_no, item.category, item.project_name].filter(Boolean).join(' · ');
            break;
        case 'nodes':
            name = item.name || item.node_id || '';
            meta = [item.node_id, item.project_name, item.status].filter(Boolean).join(' · ');
            break;
        case 'sops':
            name = item.name || item.sop_id || '';
            meta = [item.sop_id, item.category, item.sop_type].filter(Boolean).join(' · ');
            break;
        case 'rag_files':
            name = item.name || item.doc_id || '';
            meta = [item.doc_id, `${item.chunk_count} chunks`, item.status].filter(Boolean).join(' · ');
            break;
        default:
            name = item.name || '';
            meta = '';
    }
    return `<div class="resource-item">
        <div class="rname">${escapeHtml(name)}</div>
        ${meta ? `<div class="rmeta">${escapeHtml(meta)}</div>` : ''}
    </div>`;
}

// ── LLM 流量（mitmproxy jsonl → 增量轮询 + 手动刷新 + 自动刷新开关）──

const LLM_TRACE_POLL_MS = 3000;   // 增量轮询间隔
let _llmTraceRows = [];           // 当前已展示的行（累积）
let _llmTraceOffset = null;       // 上次 next_offset；null=下次全量拉最近 N
let _llmTraceTimer = null;        // 轮询定时器
let _llmTracePolling = true;      // 自动刷新开关

// 选中左侧「LLM 流量」条目：右侧显示流量表，启动增量轮询
function selectLlmTrace() {
    _selectedName = LLM_TRACE_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(LLM_TRACE_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#mcp-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#llm-trace-panel').hidden = false;

    loadLlmTrace(true);      // 进入时全量拉最近 N 条
    startLlmTracePolling();
}

function startLlmTracePolling() {
    stopLlmTracePolling();
    _llmTracePolling = true;
    syncLlmTracePollUi();
    _llmTraceTimer = setInterval(() => {
        // 面板被切走即自停，避免后台空转打接口
        if (q('#llm-trace-panel').hidden) {
            stopLlmTracePolling();
            return;
        }
        if (_llmTracePolling) loadLlmTrace(false);
    }, LLM_TRACE_POLL_MS);
}

function stopLlmTracePolling() {
    if (_llmTraceTimer) {
        clearInterval(_llmTraceTimer);
        _llmTraceTimer = null;
    }
}

function syncLlmTracePollUi() {
    const btn = q('#llm-trace-auto-btn');
    if (btn) btn.textContent = _llmTracePolling ? '自动刷新：开' : '自动刷新：关';
    const status = q('#llm-trace-status');
    if (status) status.textContent = _llmTracePolling ? '轮询中（3s）' : '已暂停';
}

async function loadLlmTrace(reset) {
    const empty = q('#llm-trace-empty');
    try {
        const params = new URLSearchParams();
        params.set('limit', '100');
        if (!reset && _llmTraceOffset != null) {
            params.set('offset', String(_llmTraceOffset));
        }
        const resp = await fetch(API_CONSOLE + '/llm-trace?' + params.toString());
        const json = await resp.json();
        if (json.code !== 0 || !json.data) {
            if (reset) {
                _llmTraceRows = [];
                renderLlmTrace();
            }
            return;
        }
        const d = json.data;
        // 文件被截断/重建（next_offset 回退）→ 清空重拉
        if (!reset && _llmTraceOffset != null && d.next_offset < _llmTraceOffset) {
            _llmTraceOffset = null;
            loadLlmTrace(true);
            return;
        }
        _llmTraceOffset = d.next_offset;
        if (reset) _llmTraceRows = [];
        _llmTraceRows = _llmTraceRows.concat(d.rows || []);
        if (_llmTraceRows.length > 200) _llmTraceRows = _llmTraceRows.slice(-200);
        renderLlmTrace();
        const count = q('#llm-trace-count');
        if (count) count.textContent = `共 ${d.total} 条 · 已展示 ${_llmTraceRows.length}`;
        if (!d.available) empty.hidden = false;
    } catch (e) {
        // 网络错误不打断轮询
    }
}

function renderLlmTrace() {
    const tbody = q('#llm-trace-tbody');
    const empty = q('#llm-trace-empty');
    if (!_llmTraceRows.length) {
        tbody.innerHTML = '';
        empty.hidden = false;
        empty.textContent = '尚未产生流量（等待 LLM 调用）';
        return;
    }
    empty.hidden = true;
    tbody.innerHTML = _llmTraceRows.map((r, i) => {
        const usage = r.usage || {};
        const totalTok = usage.total_tokens != null ? usage.total_tokens : '';
        const msgCount = r.messages_count != null ? r.messages_count : '';
        return `<tr class="llm-row" data-idx="${i}">
            <td class="log-time">${fmtLogTime(r.timestamp)}</td>
            <td class="log-module">${escapeHtml(r.model || '—')}</td>
            <td class="llm-msgs">${msgCount === '' ? '—' : msgCount}</td>
            <td class="llm-finish">${escapeHtml(r.finish_reason || '—')}</td>
            <td class="llm-tokens">${totalTok === '' ? '—' : totalTok}</td>
        </tr>
        <tr class="llm-detail-row" data-idx="${i}" hidden>
            <td colspan="5">${renderLlmDetail(r)}</td>
        </tr>`;
    }).join('');

    tbody.querySelectorAll('.llm-row').forEach(row => {
        row.addEventListener('click', () => {
            const detail = tbody.querySelector(`.llm-detail-row[data-idx="${row.dataset.idx}"]`);
            if (detail) detail.hidden = !detail.hidden;
        });
    });
}

function renderLlmDetail(r) {
    const req = r.request || {};
    const resp = r.response || {};

    let reqHtml = '';
    const messages = Array.isArray(req.messages) ? req.messages : [];
    if (messages.length) {
        reqHtml = messages.map(m => {
            const role = m.role || '?';
            let content = m.content;
            if (content != null && typeof content !== 'string') content = JSON.stringify(content);
            return `<div class="llm-msg"><span class="llm-role llm-role-${escapeAttr(String(role))}">${escapeHtml(role)}</span><pre class="llm-pre">${escapeHtml(content == null ? '' : content)}</pre></div>`;
        }).join('');
    } else {
        reqHtml = `<pre class="llm-pre">${escapeHtml(JSON.stringify(req, null, 2))}</pre>`;
    }

    let respHtml = '';
    const choices = Array.isArray(resp.choices) ? resp.choices : [];
    if (choices.length) {
        respHtml = choices.map(c => {
            const msg = c.message || {};
            let content = msg.content;
            if (content != null && typeof content !== 'string') content = JSON.stringify(content);
            const reasoning = msg.reasoning_content || '';
            let s = `<pre class="llm-pre">${escapeHtml(content == null ? '' : content)}</pre>`;
            if (reasoning) s += `<div class="llm-reason-title">reasoning_content（思维链）</div><pre class="llm-pre llm-reason">${escapeHtml(reasoning)}</pre>`;
            return s;
        }).join('');
    } else {
        respHtml = `<pre class="llm-pre">${escapeHtml(JSON.stringify(resp, null, 2))}</pre>`;
    }

    const usage = r.usage || {};
    const usageHtml = Object.keys(usage).length
        ? `<div class="llm-usage">usage：<code>${escapeHtml(JSON.stringify(usage))}</code></div>` : '';

    return `<div class="llm-detail">
        <div class="llm-detail-title">Request（messages）</div>${reqHtml}
        <div class="llm-detail-title">Response</div>${respHtml}${usageHtml}
    </div>`;
}

q('#llm-trace-refresh-btn').addEventListener('click', () => loadLlmTrace(true));
q('#llm-trace-auto-btn').addEventListener('click', () => {
    _llmTracePolling = !_llmTracePolling;
    syncLlmTracePollUi();
});


// ── 启动 ──

loadEnv();
loadList();
ensureGlobalUsers();


// ══════════════════════════════════════════════════════════════════════════════
//  MCP 配置（Server 增删改 + 开关 + 在线探测）
// ══════════════════════════════════════════════════════════════════════════════

let _mcpServers = [];                 // 最近一次 GET /mcp/servers 的结果
let _mcpProbeState = {};              // name -> { online, tool_count, tools, error }

function selectMcp() {
    _selectedName = MCP_ENTRY_NAME;
    _schema = null;
    _pendingValues = null;

    setActiveEntry(MCP_ENTRY_NAME);

    q('#empty-state').hidden = true;
    q('#runner').hidden = true;
    q('.resource-display').hidden = true;
    q('#upload-panel').hidden = true;
    q('#rag-panel').hidden = true;
    q('#node-table-panel').hidden = true;
    q('#logs-panel').hidden = true;
    q('#self-check-panel').hidden = true;
    q('#prompt-panel').hidden = true;
    q('#llm-trace-panel').hidden = true;
    q('#sop-display-panel').hidden = true;
    q('#panorama-nodes-panel').hidden = true;
    q('#project-events-panel').hidden = true;
    q('#test-cases-panel').hidden = true;
    q('#session-pool-panel').hidden = true;
    q('#session-archive-panel').hidden = true;
    q('#mcp-panel').hidden = false;

    closeMcpEdit();
    loadMcpServers();
}

async function loadMcpServers() {
    q('#mcp-status').textContent = '加载中…';
    try {
        const resp = await fetch(API_CONSOLE + '/mcp/servers');
        const json = await resp.json();
        if (json.code === 0 && json.data) {
            _mcpServers = json.data.servers || [];
            q('#mcp-config-path').textContent = '配置路径：' + (json.data.config_path || '');
            renderMcpServers();
            q('#mcp-status').textContent = '';
        } else {
            q('#mcp-status').textContent = '加载失败：' + (json.message || '');
        }
    } catch (e) {
        q('#mcp-status').textContent = '加载失败：' + e.message;
    }
}

function mcpStatusBadge(probe) {
    if (!probe) return '<span class="mcp-badge mcp-badge-unknown">未探测</span>';
    if (probe.online) return `<span class="mcp-badge mcp-badge-online">在线 · ${probe.tool_count} 工具</span>`;
    return `<span class="mcp-badge mcp-badge-offline" title="${escapeAttr(probe.error || '')}">离线</span>`;
}

function renderMcpServers() {
    const el = q('#mcp-server-list');
    if (!_mcpServers.length) {
        el.innerHTML = '<div class="hint">暂无 MCP Server，点击「+ 新增 Server」添加</div>';
        return;
    }
    el.innerHTML = _mcpServers.map(s => {
        const probe = _mcpProbeState[s.name];
        const badge = mcpStatusBadge(probe);
        const conn = s.transport === 'stdio'
            ? `<code>${escapeHtml(s.command || '')} ${escapeHtml((s.args || []).join(' '))}</code>`
            : `<code>${escapeHtml(s.url || '')}</code>`;
        const toolsHtml = probe && probe.tools && probe.tools.length
            ? `<div class="mcp-card-tools">${probe.tools.map(t =>
                `<code class="mcp-tool-chip" title="${escapeAttr(t.description || '')}">${escapeHtml(t.name)}</code>`
            ).join('')}</div>`
            : '';
        return `<div class="mcp-card" data-name="${escapeAttr(s.name)}">
            <div class="mcp-card-head">
                <div class="mcp-card-title">
                    <span class="mcp-card-name">${escapeHtml(s.name)}</span>
                    ${s.description ? `<span class="mcp-card-desc">${escapeHtml(s.description)}</span>` : ''}
                </div>
                <label class="mcp-switch" title="启用 / 禁用">
                    <input type="checkbox" class="mcp-toggle" data-name="${escapeAttr(s.name)}" ${s.enabled ? 'checked' : ''}>
                    <span class="mcp-switch-slider"></span>
                </label>
                ${badge}
                <span class="mcp-tag">${escapeHtml(s.transport)}</span>
            </div>
            <div class="mcp-card-body">
                <div class="mcp-card-line">${conn}</div>
                ${toolsHtml}
            </div>
            <div class="mcp-card-actions">
                <button type="button" class="btn mcp-probe-btn" data-name="${escapeAttr(s.name)}">探测</button>
                <button type="button" class="btn mcp-edit-btn" data-name="${escapeAttr(s.name)}">编辑</button>
                <button type="button" class="btn danger mcp-del-btn" data-name="${escapeAttr(s.name)}">删除</button>
            </div>
        </div>`;
    }).join('');
}

async function probeMcpServer(name) {
    q('#mcp-status').textContent = '探测中：' + name + ' …';
    try {
        const resp = await fetch(API_CONSOLE + '/mcp/probe?name=' + encodeURIComponent(name), { method: 'POST' });
        const json = await resp.json();
        if (json.code === 0 && json.data) {
            _mcpProbeState[name] = json.data;
        } else {
            _mcpProbeState[name] = { online: false, tool_count: 0, tools: [], error: json.message || 'unknown' };
        }
    } catch (e) {
        _mcpProbeState[name] = { online: false, tool_count: 0, tools: [], error: e.message };
    }
    renderMcpServers();
    q('#mcp-status').textContent = '';
}

function kvToText(obj) {
    if (!obj) return '';
    return Object.entries(obj).map(([k, v]) => k + '=' + v).join('\n');
}

function parseKv(text) {
    const out = {};
    (text || '').split('\n').forEach(line => {
        const s = line.trim();
        if (!s) return;
        const i = s.indexOf('=');
        if (i <= 0) return;
        out[s.slice(0, i).trim()] = s.slice(i + 1).trim();
    });
    return out;
}

function applyMcpTransportFields(transport) {
    // stdio 只需命令 + 参数；远程（sse / streamable_http）只需 URL + Headers
    const isStdio = transport === 'stdio';
    q('#mcp-f-command-field').hidden = !isStdio;
    q('#mcp-f-args-field').hidden = !isStdio;
    q('#mcp-f-url-field').hidden = isStdio;
    q('#mcp-f-headers-field').hidden = isStdio;
}

function openMcpEdit(server) {
    const isEdit = !!server;
    q('#mcp-edit-title').textContent = isEdit ? '编辑 MCP Server：' + server.name : '新增 MCP Server';
    q('#mcp-f-name').value = isEdit ? server.name : '';
    q('#mcp-f-name').disabled = isEdit;   // 编辑时名称作为 key 不可改
    q('#mcp-f-desc').value = isEdit ? (server.description || '') : '';
    q('#mcp-f-transport').value = isEdit ? server.transport : 'stdio';
    q('#mcp-f-command').value = isEdit ? (server.command || '') : '';
    q('#mcp-f-args').value = isEdit ? (server.args || []).join(' ') : '';
    q('#mcp-f-url').value = isEdit ? (server.url || '') : '';
    q('#mcp-f-headers').value = isEdit ? kvToText(server.headers) : '';
    applyMcpTransportFields(q('#mcp-f-transport').value);
    q('#mcp-edit-status').textContent = '';
    q('#mcp-edit-box').hidden = false;
}

function closeMcpEdit() {
    q('#mcp-edit-box').hidden = true;
    q('#mcp-f-name').disabled = false;
}

function collectMcpForm() {
    const transport = q('#mcp-f-transport').value;
    const isStdio = transport === 'stdio';
    const args = (q('#mcp-f-args').value || '').trim().split(/\s+/).filter(Boolean);
    return {
        name: q('#mcp-f-name').value.trim(),
        description: q('#mcp-f-desc').value.trim(),
        enabled: true,
        transport: transport,
        command: isStdio ? q('#mcp-f-command').value.trim() : '',
        args: isStdio ? args : [],
        env: {},
        cwd: '',
        url: isStdio ? '' : q('#mcp-f-url').value.trim(),
        headers: isStdio ? {} : parseKv(q('#mcp-f-headers').value),
        tool_prefix: '',
        category: 'base',
        permission_flag: 'all',
        write_mode: 'read',
        timeout_seconds: 30,
    };
}

async function saveMcpServer() {
    const payload = collectMcpForm();
    if (!payload.name) {
        q('#mcp-edit-status').textContent = '名称不能为空';
        return;
    }
    q('#mcp-edit-status').textContent = '保存中…';
    try {
        const resp = await fetch(API_CONSOLE + '/mcp/server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const json = await resp.json();
        if (json.code === 0) {
            q('#mcp-edit-status').textContent = '已保存（重启 emily-core 后生效）';
            closeMcpEdit();
            delete _mcpProbeState[payload.name];
            loadMcpServers();
        } else {
            q('#mcp-edit-status').textContent = '保存失败：' + (json.message || '');
        }
    } catch (e) {
        q('#mcp-edit-status').textContent = '保存失败：' + e.message;
    }
}

async function deleteMcpServer(name) {
    if (!confirm('确认删除 MCP Server「' + name + '」？')) return;
    try {
        const resp = await fetch(API_CONSOLE + '/mcp/server?name=' + encodeURIComponent(name), { method: 'DELETE' });
        const json = await resp.json();
        if (json.code === 0) {
            delete _mcpProbeState[name];
            loadMcpServers();
        } else {
            alert('删除失败：' + (json.message || ''));
        }
    } catch (e) {
        alert('删除失败：' + e.message);
    }
}

async function toggleMcpServer(name, enabled) {
    try {
        const resp = await fetch(API_CONSOLE + '/mcp/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, enabled: enabled }),
        });
        const json = await resp.json();
        if (json.code === 0) {
            const s = _mcpServers.find(x => x.name === name);
            if (s) s.enabled = enabled;
            q('#mcp-status').textContent = '已保存（重启 emily-core 后生效）';
        } else {
            alert('切换失败：' + (json.message || ''));
            loadMcpServers();
        }
    } catch (e) {
        alert('切换失败：' + e.message);
        loadMcpServers();
    }
}

q('#mcp-f-transport').addEventListener('change', (ev) => applyMcpTransportFields(ev.target.value));
q('#mcp-add-btn').addEventListener('click', () => openMcpEdit(null));
q('#mcp-refresh-btn').addEventListener('click', () => loadMcpServers());
q('#mcp-save-btn').addEventListener('click', saveMcpServer);
q('#mcp-cancel-btn').addEventListener('click', closeMcpEdit);
q('#mcp-server-list').addEventListener('click', (ev) => {
    const probeBtn = ev.target.closest('.mcp-probe-btn');
    const editBtn = ev.target.closest('.mcp-edit-btn');
    const delBtn = ev.target.closest('.mcp-del-btn');
    if (probeBtn) probeMcpServer(probeBtn.dataset.name);
    else if (editBtn) openMcpEdit(_mcpServers.find(s => s.name === editBtn.dataset.name));
    else if (delBtn) deleteMcpServer(delBtn.dataset.name);
});
q('#mcp-server-list').addEventListener('change', (ev) => {
    if (ev.target.classList.contains('mcp-toggle')) {
        toggleMcpServer(ev.target.dataset.name, ev.target.checked);
    }
});