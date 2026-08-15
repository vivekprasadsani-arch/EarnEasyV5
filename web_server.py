import os
import logging
import asyncio
import base64
from aiohttp import web
import database as db
import bot_backend as backend
import config

logger = logging.getLogger(__name__)

# Premium CSS and single-page Admin Panel HTML
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EarnEasy C88ZZ Admin Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0c10;
            --panel-bg: #11131c;
            --card-bg: #181b28;
            --border-color: #222636;
            --text-primary: #f1f3f9;
            --text-secondary: #8e95a5;
            --accent-blue: #2563eb;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --accent-yellow: #f59e0b;
        }
        
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Plus Jakarta Sans', sans-serif;
            -webkit-font-smoothing: antialiased;
        }
        
        body {
            background-color: var(--bg-color);
            color: var(--text-primary);
            padding: 2rem;
            min-height: 100vh;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--border-color);
        }
        
        h1 {
            font-size: 1.8rem;
            font-weight: 700;
            letter-spacing: -0.025em;
        }
        
        .badge-admin {
            background: rgba(37, 99, 235, 0.15);
            color: #60a5fa;
            padding: 0.4rem 0.8rem;
            border-radius: 9999px;
            font-size: 0.8rem;
            font-weight: 600;
        }
        
        /* Stats Cards */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        
        .stat-card {
            background-color: var(--panel-bg);
            border: 1px solid var(--border-color);
            padding: 1.5rem;
            border-radius: 12px;
            position: relative;
            overflow: hidden;
        }
        
        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background-color: var(--accent-blue);
        }
        
        .stat-card.success::before {
            background-color: var(--accent-green);
        }
        
        .stat-card.warning::before {
            background-color: var(--accent-yellow);
        }

        .stat-card.danger::before {
            background-color: var(--accent-red);
        }
        
        .stat-title {
            font-size: 0.85rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 0.5rem;
        }
        
        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            letter-spacing: -0.03em;
        }
        
        /* User Table */
        .panel {
            background-color: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 2rem;
        }
        
        .panel-header {
            padding: 1.5rem;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .panel-title {
            font-size: 1.1rem;
            font-weight: 600;
        }
        
        .table-responsive {
            overflow-x: auto;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }
        
        th {
            background-color: rgba(255, 255, 255, 0.02);
            color: var(--text-secondary);
            font-size: 0.8rem;
            text-transform: uppercase;
            font-weight: 600;
            padding: 1rem 1.5rem;
            border-bottom: 1px solid var(--border-color);
        }
        
        td {
            padding: 1.2rem 1.5rem;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.9rem;
            vertical-align: middle;
        }
        
        tr:last-child td {
            border-bottom: none;
        }
        
        /* Badges */
        .status-badge {
            display: inline-flex;
            align-items: center;
            padding: 0.25rem 0.6rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        .status-approved {
            background-color: rgba(16, 185, 129, 0.15);
            color: #34d399;
        }
        
        .status-pending {
            background-color: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
        }
        
        .status-rejected {
            background-color: rgba(239, 68, 68, 0.15);
            color: #f87171;
        }
        
        /* Buttons */
        .btn {
            background-color: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 0.4rem 0.8rem;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
        }
        
        .btn:hover {
            background-color: rgba(255, 255, 255, 0.05);
            border-color: var(--text-secondary);
        }
        
        .btn-approve {
            background-color: rgba(16, 185, 129, 0.1);
            border-color: rgba(16, 185, 129, 0.3);
            color: #34d399;
        }
        
        .btn-approve:hover {
            background-color: var(--accent-green);
            color: white;
            border-color: var(--accent-green);
        }
        
        .btn-reject {
            background-color: rgba(239, 68, 68, 0.1);
            border-color: rgba(239, 68, 68, 0.3);
            color: #f87171;
        }
        
        .btn-reject:hover {
            background-color: var(--accent-red);
            color: white;
            border-color: var(--accent-red);
        }
        
        .actions-cell {
            display: flex;
            gap: 0.5rem;
        }
        
        /* Expandable section */
        .expandable-row {
            cursor: pointer;
        }
        
        .expandable-row:hover {
            background-color: rgba(255, 255, 255, 0.01);
        }
        
        .details-row {
            background-color: rgba(0, 0, 0, 0.15);
            display: none;
        }
        
        .details-container {
            padding: 1.5rem;
            border-left: 2px solid var(--accent-blue);
        }
        
        .ref-title {
            font-size: 0.95rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: var(--text-primary);
            display: flex;
            justify-content: space-between;
        }
        
        .ref-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1rem;
        }
        
        .ref-card {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        
        .ref-number {
            font-size: 0.95rem;
            font-weight: 600;
            color: #f1f3f9;
        }
        
        .ref-meta {
            font-size: 0.75rem;
            color: var(--text-secondary);
            display: flex;
            justify-content: space-between;
        }
        
        .online-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--accent-red);
            display: inline-block;
            margin-right: 4px;
        }
        
        .online-dot.active {
            background-color: var(--accent-green);
        }
        
        .loading {
            padding: 3rem;
            text-align: center;
            color: var(--text-secondary);
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>EarnEasy C88ZZ Admin Portal</h1>
                <p style="color: var(--text-secondary); font-size: 0.85rem; margin-top: 0.25rem;">Real-time management dashboard</p>
            </div>
            <div style="display: flex; gap: 0.5rem; align-items: center;">
                <button class="btn" onclick="changeAdminSettings()">🔑 Panel Settings</button>
                <span class="badge-admin">Root Administrator</span>
            </div>
        </header>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-title">Total Users</div>
                <div class="stat-value" id="stat-users">-</div>
            </div>
            <div class="stat-card success">
                <div class="stat-title">Active Links (WhatsApp)</div>
                <div class="stat-value" id="stat-links">-</div>
            </div>
            <div class="stat-card warning">
                <div class="stat-title">Pending Users</div>
                <div class="stat-value" id="stat-pending">-</div>
            </div>
            <div class="stat-card danger">
                <div class="stat-title">Pending Withdrawals</div>
                <div class="stat-value" id="stat-withdrawals">-</div>
            </div>
        </div>
        
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Telegram Users & Referrals</div>
                <button class="btn" onclick="loadData()">🔄 Refresh</button>
            </div>
            <div class="table-responsive">
                <table id="users-table">
                    <thead>
                        <tr>
                            <th>User ID</th>
                            <th>Username</th>
                            <th>First Name</th>
                            <th>Status</th>
                            <th>Main Mobile</th>
                            <th>Main Refer Code</th>
                            <th>C88ZZ Default Pass</th>
                            <th>Referred Accounts</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="users-tbody">
                        <tr>
                            <td colspan="9" class="loading">Loading dashboard data...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Withdrawal Requests</div>
            </div>
            <div class="table-responsive">
                <table id="withdrawals-table">
                    <thead>
                        <tr>
                            <th>User ID</th>
                            <th>Payment Method</th>
                            <th>Payment Details</th>
                            <th>Points</th>
                            <th>USD Value</th>
                            <th>Status</th>
                            <th>Requested On</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="withdrawals-tbody">
                        <tr>
                            <td colspan="8" class="loading">Loading withdrawal requests...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        async function loadData() {
            try {
                const response = await fetch('/api/users');
                const data = await response.json();
                
                // Update stats
                document.getElementById('stat-users').innerText = data.stats.total_users;
                document.getElementById('stat-links').innerText = data.stats.total_linked_accounts;
                document.getElementById('stat-pending').innerText = data.stats.pending_users;
                document.getElementById('stat-withdrawals').innerText = data.stats.pending_withdrawals;
                
                // 1. Populate Users Table
                const tbody = document.getElementById('users-tbody');
                tbody.innerHTML = '';
                
                if (data.users.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="9" class="loading">No users found in database.</td></tr>';
                } else {
                    data.users.forEach(user => {
                        const userAccounts = data.accounts.filter(acc => acc.user_id === user.user_id);
                        
                        const tr = document.createElement('tr');
                        tr.className = 'expandable-row';
                        tr.onclick = (e) => {
                            if (e.target.closest('button') || e.target.closest('input')) return;
                            toggleRow(user.user_id);
                        };
                        
                        let actionButtons = '';
                        if (user.status === 'pending') {
                            actionButtons = `
                                <div class="actions-cell">
                                    <button class="btn btn-approve" onclick="actionUser(${user.user_id}, 'approve')">Approve ✅</button>
                                    <button class="btn btn-reject" onclick="actionUser(${user.user_id}, 'reject')">Reject ❌</button>
                                </div>
                            `;
                        } else if (user.status === 'approved') {
                            actionButtons = `<button class="btn btn-reject" onclick="actionUser(${user.user_id}, 'reject')">Revoke ❌</button>`;
                        } else {
                            actionButtons = `<button class="btn btn-approve" onclick="actionUser(${user.user_id}, 'approve')">Approve ✅</button>`;
                        }
                        
                        const customPwd = user.custom_password || '53561106@Roni';
                        const pwdInputHTML = `
                            <div style="display: flex; align-items: center; gap: 4px;" onclick="event.stopPropagation()">
                                <input type="text" id="pwd-${user.user_id}" value="${customPwd}" style="background: #222636; border: 1px solid var(--border-color); color: white; padding: 0.25rem 0.5rem; border-radius: 6px; width: 140px; font-size: 0.8rem; outline: none;" onclick="event.stopPropagation()">
                                <button class="btn" onclick="saveUserPassword(event, ${user.user_id})" style="padding: 0.25rem 0.5rem; font-size: 0.85rem;">💾</button>
                            </div>
                        `;
                        
                        tr.innerHTML = `
                            <td><code>${user.user_id}</code></td>
                            <td>${user.username ? '@' + user.username : '<span style="color: var(--text-secondary)">None</span>'}</td>
                            <td>${user.first_name || ''}</td>
                            <td><span class="status-badge status-${user.status}">${user.status.toUpperCase()}</span></td>
                            <td><code>${user.main_mobile || 'Not Registered'}</code></td>
                            <td><code>${user.main_invite_code || 'None'}</code></td>
                            <td>${pwdInputHTML}</td>
                            <td><span style="font-weight: 600;">${userAccounts.length} accounts</span></td>
                            <td>${actionButtons}</td>
                        `;
                        tbody.appendChild(tr);
                        
                        const detailsTr = document.createElement('tr');
                        detailsTr.id = `details-${user.user_id}`;
                        detailsTr.className = 'details-row';
                        
                        let refCardsHTML = '';
                        if (userAccounts.length === 0) {
                            refCardsHTML = '<div style="color: var(--text-secondary); font-size: 0.85rem;">No WhatsApp accounts linked yet under this user.</div>';
                        } else {
                            refCardsHTML = '<div class="ref-grid">';
                            userAccounts.forEach(acc => {
                                const isOnline = acc.is_linked;
                                refCardsHTML += `
                                    <div class="ref-card">
                                        <div class="ref-number">📱 ${acc.email}</div>
                                        <div class="ref-meta">
                                            <span>Invite: <code>${acc.invite_code}</code></span>
                                            <span>
                                                <span class="online-dot ${isOnline ? 'active' : ''}"></span>
                                                ${isOnline ? 'ONLINE' : 'OFFLINE'}
                                            </span>
                                        </div>
                                    </div>
                                `;
                            });
                            refCardsHTML += '</div>';
                        }
                        
                        detailsTr.innerHTML = `
                            <td colspan="9">
                                <div class="details-container">
                                    <div class="ref-title">
                                        <span>Detailed WhatsApp Links</span>
                                        <span>Invite Code used: <code>${user.main_invite_code || 'ZF5998'}</code></span>
                                    </div>
                                    ${refCardsHTML}
                                </div>
                            </td>
                        `;
                        tbody.appendChild(detailsTr);
                    });
                }

                // 2. Populate Withdrawals Table
                const wdTbody = document.getElementById('withdrawals-tbody');
                wdTbody.innerHTML = '';
                
                if (!data.withdrawals || data.withdrawals.length === 0) {
                    wdTbody.innerHTML = '<tr><td colspan="8" class="loading">No withdrawal requests found.</td></tr>';
                } else {
                    data.withdrawals.forEach(wd => {
                        const tr = document.createElement('tr');
                        
                        let actionButtons = '';
                        if (wd.status === 'pending') {
                            actionButtons = `
                                <div class="actions-cell">
                                    <button class="btn btn-approve" onclick="actionWithdrawal(${wd.id}, 'approve')">Approve ✅</button>
                                    <button class="btn btn-reject" onclick="actionWithdrawal(${wd.id}, 'reject')">Reject ❌</button>
                                </div>
                            `;
                        } else {
                            actionButtons = `<span style="color: var(--text-secondary); font-size: 0.8rem; font-weight: 500;">Processed</span>`;
                        }
                        
                        const statusClass = wd.status === 'approved' ? 'status-approved' : (wd.status === 'rejected' ? 'status-rejected' : 'status-pending');
                        const dateStr = new Date(wd.created_at).toLocaleString();
                        
                        tr.innerHTML = `
                            <td><code>${wd.user_id}</code></td>
                            <td><span style="font-weight: 600;">${wd.payment_method}</span></td>
                            <td><code>${wd.payment_details}</code></td>
                            <td><strong>${wd.amount_points}</strong></td>
                            <td><strong style="color: #60a5fa;">$${wd.amount_usd}</strong></td>
                            <td><span class="status-badge ${statusClass}">${wd.status.toUpperCase()}</span></td>
                            <td style="font-size: 0.8rem; color: var(--text-secondary);">${dateStr}</td>
                            <td>${actionButtons}</td>
                        `;
                        wdTbody.appendChild(tr);
                    });
                }
                
            } catch (err) {
                console.error("Error loading dashboard data:", err);
            }
        }
        
        function toggleRow(userId) {
            const row = document.getElementById(`details-${userId}`);
            if (row.style.display === 'table-row') {
                row.style.display = 'none';
            } else {
                row.style.display = 'table-row';
            }
        }
        
        async function actionUser(userId, action) {
            if (!confirm(`Are you sure you want to ${action} this user?`)) return;
            try {
                const response = await fetch(`/api/users/${userId}/${action}`, { method: 'POST' });
                const res = await response.json();
                if (res.success) {
                    loadData();
                } else {
                    alert("Operation failed: " + res.message);
                }
            } catch (err) {
                alert("Error calling admin API: " + err);
            }
        }

        async function saveUserPassword(event, userId) {
            event.stopPropagation();
            const password = document.getElementById(`pwd-${userId}`).value.trim();
            if (!password) {
                alert("Password cannot be empty!");
                return;
            }
            
            try {
                const response = await fetch(`/api/users/${userId}/password`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password })
                });
                const res = await response.json();
                if (res.success) {
                    alert("User password updated successfully!");
                    loadData();
                } else {
                    alert("Failed to update password: " + res.message);
                }
            } catch (err) {
                alert("Error: " + err);
            }
        }

        async function changeAdminSettings() {
            const username = prompt("Enter new Admin Panel Username:");
            if (username === null) return;
            const password = prompt("Enter new Admin Panel Password:");
            if (password === null) return;
            
            if (!username.trim() || !password.trim()) {
                alert("Username and password cannot be empty!");
                return;
            }
            
            try {
                const response = await fetch('/api/admin/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                const res = await response.json();
                if (res.success) {
                    alert("Admin credentials updated! Refreshing page to request re-login.");
                    location.reload();
                } else {
                    alert("Failed to update settings: " + res.message);
                }
            } catch (err) {
                alert("Error: " + err);
            }
        }

        async function actionWithdrawal(wdId, action) {
            if (!confirm(`Are you sure you want to ${action} this withdrawal request?`)) return;
            try {
                const response = await fetch(`/api/withdrawals/${wdId}/${action}`, { method: 'POST' });
                const res = await response.json();
                if (res.success) {
                    loadData();
                } else {
                    alert("Operation failed: " + res.message);
                }
            } catch (err) {
                alert("Error calling withdrawal API: " + err);
            }
        }
        
        // Initial load and poll every 10 seconds
        loadData();
        setInterval(loadData, 10000);
    </script>
</body>
</html>
"""

# Native HTTP Basic Auth Middleware
@web.middleware
async def auth_middleware(request, handler):
    # Skip auth for liveness/health check probes
    if request.path in ('/health', '/healthz', '/api/ping'):
        return await handler(request)
        
    auth_header = request.headers.get('Authorization')
    authorized = False
    
    if auth_header and auth_header.startswith('Basic '):
        try:
            encoded = auth_header.split(' ')[1]
            decoded = base64.b64decode(encoded).decode('utf-8')
            user, password = decoded.split(':', 1)
            
            # Fetch admin record from Supabase
            admin_user = await db.get_user(config.ADMIN_USER_ID)
            db_user = admin_user.get("admin_panel_user") if admin_user else None
            db_pass = admin_user.get("admin_panel_pass") if admin_user else None
            
            # Fallbacks if credentials have not been configured yet
            if not db_user:
                db_user = "admin"
            if not db_pass:
                db_pass = "53561106@Roni"
                
            if user == db_user and password == db_pass:
                authorized = True
        except Exception as e:
            logger.error(f"Basic Auth decoding error: {e}")
            
    if not authorized:
        return web.Response(
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="Admin Dashboard"'},
            text="Unauthorized"
        )
        
    return await handler(request)

async def handle_index(request):
    return web.Response(text=INDEX_HTML, content_type="text/html")

async def get_users_api(request):
    try:
        users = await db.get_all_users_admin()
        accounts = await db.get_all_accounts_admin()
        withdrawals = await db.get_all_withdrawals_admin()
        
        # Calculate stats
        total_users = len(users)
        pending_users = sum(1 for u in users if u.get("status") == "pending")
        total_linked_accounts = sum(1 for a in accounts if a.get("is_linked") is True)
        pending_withdrawals = sum(1 for w in withdrawals if w.get("status") == "pending")
        
        return web.json_response({
            "users": users,
            "accounts": accounts,
            "withdrawals": withdrawals,
            "stats": {
                "total_users": total_users,
                "pending_users": pending_users,
                "total_linked_accounts": total_linked_accounts,
                "pending_withdrawals": pending_withdrawals
            }
        })
    except Exception as e:
        logger.error(f"API Error fetching dashboard data: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def post_user_action(request):
    user_id = int(request.match_info['user_id'])
    action = request.match_info['action'] # approve or reject
    
    status_val = "approved" if action == "approve" else "rejected"
    try:
        await db.update_user_status(user_id, status_val)
        
        # If approved, notify the user on Telegram
        if status_val == "approved":
            from tg_bot import bot, main_keyboard
            try:
                await bot.send_message(
                    user_id, 
                    "🎉 **Congratulations!** Your account has been approved by the Admin!\n"
                    "You can now use the menu to add WhatsApp and start earning.",
                    reply_markup=main_keyboard()
                )
            except Exception as notify_err:
                logger.error(f"Failed to notify approved user {user_id}: {notify_err}")
                
        return web.json_response({"success": True})
    except Exception as e:
        logger.error(f"API Error updating user status: {e}")
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def post_user_password(request):
    """Sets a custom default C88ZZ account password for a specific user."""
    try:
        user_id = int(request.match_info['user_id'])
        data = await request.json()
        password = data.get("password", "").strip()
        
        if not password:
            return web.json_response({"success": False, "message": "Password cannot be empty"}, status=400)
            
        await db.set_user_password(user_id, password)
        return web.json_response({"success": True})
    except Exception as e:
        logger.error(f"API Error updating user default password: {e}")
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def post_admin_settings(request):
    """Updates the admin portal credentials."""
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        
        if not username or not password:
            return web.json_response({"success": False, "message": "Credentials cannot be empty"}, status=400)
            
        await db.update_admin_credentials(username, password)
        return web.json_response({"success": True})
    except Exception as e:
        logger.error(f"API Error updating admin settings: {e}")
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def post_withdrawal_action(request):
    """Approves or rejects a withdrawal request from the web panel."""
    wd_id = int(request.match_info['wd_id'])
    action = request.match_info['action'] # approve or reject
    
    status_val = "approved" if action == "approve" else "rejected"
    try:
        wd = await db.get_withdrawal_by_id(wd_id)
        if not wd:
            return web.json_response({"success": False, "message": "Withdrawal request not found"}, status=404)
            
        if wd.get("status") != "pending":
            return web.json_response({"success": False, "message": f"Withdrawal is already {wd.get('status')}"}, status=400)
            
        await db.update_withdrawal_status(wd_id, status_val)
        
        # Notify user on Telegram
        from tg_bot import bot
        try:
            if status_val == "approved":
                await bot.send_message(
                    wd.get("user_id"),
                    f"🎉 **Withdrawal Approved!**\n\n"
                    f"Your request for `{wd.get('amount_points')} Points` (${wd.get('amount_usd')} USD) via **{wd.get('payment_method')}** has been approved and paid by the Admin!"
                )
            else:
                await bot.send_message(
                    wd.get("user_id"),
                    f"❌ **Withdrawal Rejected**\n\n"
                    f"Your request for `{wd.get('amount_points')} Points` (${wd.get('amount_usd')} USD) via **{wd.get('payment_method')}** was rejected by the Admin."
                )
        except Exception as notify_err:
            logger.error(f"Failed to notify user {wd.get('user_id')} on web withdrawal action: {notify_err}")
            
        return web.json_response({"success": True})
    except Exception as e:
        logger.error(f"API Error updating withdrawal status from web: {e}")
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def handle_ping(request):
    return web.Response(text="pong")

async def handle_healthz(request):
    try:
        await db.ping()
        return web.Response(text="ok")
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return web.Response(text="db unavailable", status=503)

async def start_server():
    """Initializes and runs the web app server concurrently on Render PORT."""
    # Register basic auth middleware
    app = web.Application(middlewares=[auth_middleware])
    
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/users', get_users_api)
    app.router.add_post('/api/users/{user_id}/{action}', post_user_action)
    app.router.add_post('/api/users/{user_id}/password', post_user_password)
    app.router.add_post('/api/admin/settings', post_admin_settings)
    app.router.add_post('/api/withdrawals/{wd_id}/{action}', post_withdrawal_action)
    app.router.add_get('/api/ping', handle_ping)
    app.router.add_get('/health', handle_healthz)
    app.router.add_get('/healthz', handle_healthz)
    
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    logger.info(f"Admin Dashboard starting on port {port}...")
    await site.start()
    
    # Keep task running indefinitely
    while True:
        await asyncio.sleep(3600)
