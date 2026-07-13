// Emily 运维看板 — 前端逻辑

const API_BASE = '/api/v1/monitor';

// ── 工具函数 ──

async function apiFetch(path) {
    const resp = await fetch(API_BASE + path);
    if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
    const json = await resp.json();
    if (json.code !== 0) throw new Error(json.message || 'API error');
    return json.data;
}

function formatIdle(seconds) {
    if (seconds < 60) return `${seconds}秒`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟`;
    return `${Math.floor(seconds / 3600)}小时`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

const CONF_LABELS = ['公开', '内部', '机密', '绝密'];
const LEVEL_LABELS = ['访客', '普通用户', '高级用户', '管理员', '高级管理员', '系统管理员', '超级管理员'];

// ── 核心状态区 ──

async function loadContainers() {
    try {
        const data = await apiFetch('/containers');
        renderContainers(data.containers || []);
        renderImAccounts(data.im_accounts || []);
    } catch (e) {
        console.error('loadContainers failed:', e);
    }
}

function renderContainers(containers) {
    const el = document.getElementById('containers');
    el.innerHTML = containers.map(c => `
        <div class="container-item">
            <span class="status-dot ${c.status}"></span>
            <span>${escapeHtml(c.name)}</span>
            <span style="color:#666;font-size:12px">${c.status === 'running' ? '运行中' : c.status === 'stopped' ? '已停止' : '未知'}</span>
        </div>
    `).join('');
}

function renderImAccounts(accounts) {
    const el = document.getElementById('im-accounts');
    const hostname = window.location.hostname;
    el.innerHTML = accounts.map(a => {
        const isConnected = a.status === 'connected';
        const statusClass = isConnected ? 'im-status-connected'
            : a.status === 'disconnected' ? 'im-status-disconnected'
            : 'im-status-no_account';
        const statusText = isConnected ? '已连接'
            : a.status === 'disconnected' ? '未登录'
            : '无账号、无连接';
        const napcatToken = a.webui_token || '';
        const napcatUrl = a.webui_available && napcatToken
            ? `http://${hostname}:6099/webui?token=${encodeURIComponent(napcatToken)}`
            : a.webui_available ? `http://${hostname}:6099/webui` : '';
        const link = napcatUrl
            ? `<a class="im-link" href="${escapeHtml(napcatUrl)}" target="_blank">NapCat管理 &rarr;</a>` : '';

        // 已登录时显示账号卡片
        let accountCard = '';
        if (isConnected && a.qq_number) {
            const nick = a.qq_nickname || 'QQ';
            accountCard = `<div class="im-account-card">
                <span class="im-account-nick">${escapeHtml(nick)}</span>
                <span class="im-account-num">${escapeHtml(a.qq_number)}</span>
            </div>`;
        }

        return `<div class="im-item ${isConnected ? 'im-item-connected' : ''}">
            <div class="im-item-left">
                <span class="im-label">${escapeHtml(a.label)}</span>
                <span class="${statusClass}">${statusText}</span>
            </div>
            ${accountCard}
            <div class="im-item-right">${link}</div>
        </div>`;
    }).join('');
}

// ── Session 池 ──

async function loadSessions() {
    try {
        const data = await apiFetch('/sessions');
        const summary = document.getElementById('session-summary');
        summary.textContent = `活跃会话: ${data.total} 个 | 池运行: ${formatIdle(data.uptime_seconds)}`;

        const tbody = document.querySelector('#session-table tbody');
        tbody.innerHTML = (data.sessions || []).map(s => `
            <tr>
                <td class="cell-link" onclick="showMessages('${escapeHtml(s.conversation_id)}')">${escapeHtml(s.conversation_id.substring(0, 20))}...</td>
                <td>${formatIdle(s.idle_seconds)}</td>
                <td><span class="cell-link" onclick="showMessages('${escapeHtml(s.conversation_id)}')">[消息]</span></td>
            </tr>
        `).join('');

        if (!data.sessions || data.sessions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" style="color:#666">暂无活跃会话</td></tr>';
        }
    } catch (e) {
        console.error('loadSessions failed:', e);
    }
}

async function showMessages(conversationId) {
    try {
        const messages = await apiFetch(`/sessions/${encodeURIComponent(conversationId)}/messages?limit=5`);
        const modal = document.getElementById('modal-overlay');
        document.getElementById('modal-title').textContent = `会话消息 — ${conversationId.substring(0, 20)}...`;
        const body = document.getElementById('modal-body');
        body.innerHTML = messages.map(m => {
            const dirClass = m.direction === 'agent_to_user' ? 'msg-agent' : 'msg-user';
            const dirLabel = m.direction === 'agent_to_user' ? 'Emily → 用户' : '用户 → Emily';
            return `<div class="msg-item">
                <div class="msg-direction">${dirLabel} | <span class="${dirClass}">${escapeHtml(m.sender_name)}</span></div>
                <div class="msg-content">${escapeHtml(m.content_summary)}</div>
                <div class="msg-time">${escapeHtml(m.created_at)}</div>
            </div>`;
        }).join('') || '<div style="color:#666">暂无消息</div>';
        modal.classList.add('active');
    } catch (e) {
        console.error('showMessages failed:', e);
    }
}

// ── 全景节点 ──

async function loadNodes(projectId) {
    try {
        const params = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
        const nodes = await apiFetch('/nodes' + params);
        const tbody = document.querySelector('#node-table tbody');
        tbody.innerHTML = nodes.map(n => `
            <tr>
                <td class="cell-link" onclick="showNodeDetail('${escapeHtml(n.node_id)}')">${escapeHtml(n.node_id)}</td>
                <td>${escapeHtml(n.node_name)}</td>
                <td>${escapeHtml(n.owner_dept_id)}</td>
                <td>${escapeHtml(n.deadline)}</td>
                <td>
                    <span class="progress-bar"><span class="progress-fill" style="width:${n.progress || 0}%"></span></span>
                    <span class="progress-text">${(n.progress || 0).toFixed(1)}%</span>
                </td>
                <td><span class="status-tag status-tag-${n.status}">${escapeHtml(n.status)}</span></td>
            </tr>
        `).join('');

        if (nodes.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="color:#666">暂无节点数据</td></tr>';
        }
    } catch (e) {
        console.error('loadNodes failed:', e);
    }
}

async function showNodeDetail(nodeId) {
    try {
        const node = await apiFetch(`/nodes/${encodeURIComponent(nodeId)}`);
        const modal = document.getElementById('node-modal-overlay');
        document.getElementById('node-modal-title').textContent = `节点详情 — ${node.node_name}`;
        const body = document.getElementById('node-modal-body');
        const fields = [
            ['项目归属', node.project_id], ['节点编号', node.node_id],
            ['节点名称', node.node_name], ['主责条线', node.owner_dept_id],
            ['关联单位', node.related_company_id], ['截止时间', node.deadline],
            ['关联地块', node.land_parcel_id], ['备注', node.remark],
            ['父节点', node.parent_node_id], ['所属阶段', node.stage_id],
            ['子节点权重', node.child_weight], ['启动文档', node.startup_doc_id],
            ['进度', `${(node.progress || 0).toFixed(1)}%`],
            ['状态', node.status],
        ];
        body.innerHTML = `<table class="detail-table">${fields.map(([k, v]) =>
            `<tr><td>${k}</td><td>${escapeHtml(String(v || ''))}</td></tr>`
        ).join('')}</table>`;
        modal.classList.add('active');
    } catch (e) {
        console.error('showNodeDetail failed:', e);
    }
}

// ── 文件 ──

async function loadFiles(projectId) {
    try {
        const params = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
        const files = await apiFetch('/files' + params);
        const tbody = document.querySelector('#file-table tbody');
        tbody.innerHTML = files.map(f => `
            <tr>
                <td>${escapeHtml(f.filename)}</td>
                <td>${escapeHtml(f.file_type)}</td>
                <td>${escapeHtml(f.version)}</td>
                <td>${escapeHtml(f.uploaded_by_name)}</td>
                <td>${escapeHtml(f.created_at)}</td>
                <td><span class="conf-tag conf-${f.confidentiality}">${CONF_LABELS[f.confidentiality] || '未知'}</span></td>
            </tr>
        `).join('');

        if (files.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="color:#666">暂无文件数据</td></tr>';
        }
    } catch (e) {
        console.error('loadFiles failed:', e);
    }
}

// ── 人员 ──

async function loadUsers() {
    try {
        const users = await apiFetch('/users');
        const tbody = document.querySelector('#user-table tbody');
        tbody.innerHTML = users.map(u => `
            <tr>
                <td style="font-size:11px;color:#666">${escapeHtml(u.id.substring(0, 8))}...</td>
                <td>${escapeHtml(u.username)}</td>
                <td>${escapeHtml(u.company_name || '')}</td>
                <td>${LEVEL_LABELS[u.level] || `L${u.level}`}</td>
            </tr>
        `).join('');

        if (users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="color:#666">暂无人员数据</td></tr>';
        }
    } catch (e) {
        console.error('loadUsers failed:', e);
    }
}

// ── 项目筛选器 ──

async function loadProjectFilters() {
    try {
        const nodes = await apiFetch('/nodes');
        const projects = [...new Set(nodes.map(n => n.project_id).filter(Boolean))];
        const nodeSelect = document.getElementById('node-project-filter');
        const fileSelect = document.getElementById('file-project-filter');
        projects.forEach(p => {
            nodeSelect.innerHTML += `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`;
            fileSelect.innerHTML += `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`;
        });
    } catch (e) {
        console.error('loadProjectFilters failed:', e);
    }
}

// ── Tab 切换 ──

function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        });
    });
}

// ── 刷新按钮 ──

function initRefreshButtons() {
    // 全部刷新
    document.getElementById('btn-refresh-all').addEventListener('click', refreshAll);

    // 单面板刷新
    document.querySelectorAll('.btn-refresh-sm').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.refresh;
            if (target === 'sessions') loadSessions();
            else if (target === 'nodes') loadNodes(document.getElementById('node-project-filter').value);
            else if (target === 'files') loadFiles(document.getElementById('file-project-filter').value);
            else if (target === 'users') loadUsers();
        });
    });

    // 项目筛选
    document.getElementById('node-project-filter').addEventListener('change', e => loadNodes(e.target.value));
    document.getElementById('file-project-filter').addEventListener('change', e => loadFiles(e.target.value));
}

// ── 弹窗 ──

function initModals() {
    document.getElementById('modal-close').addEventListener('click', () => {
        document.getElementById('modal-overlay').classList.remove('active');
    });
    document.getElementById('node-modal-close').addEventListener('click', () => {
        document.getElementById('node-modal-overlay').classList.remove('active');
    });
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', e => {
            if (e.target === overlay) overlay.classList.remove('active');
        });
    });
}

// ── 主入口 ──

function refreshAll() {
    loadContainers();
    loadSessions();
    loadNodes(document.getElementById('node-project-filter').value);
    loadFiles(document.getElementById('file-project-filter').value);
    loadUsers();
}

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initRefreshButtons();
    initModals();
    refreshAll();
    loadProjectFilters();
});
