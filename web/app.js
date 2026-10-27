const state = { user: null, groups: [], group: null, selectedGroupId: null };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', headers: options.body ? { 'Content-Type': 'application/json', ...(options.headers || {}) } : (options.headers || {}), ...options });
  if (response.status === 204) return {};
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Something went wrong');
  return data;
}
function showToast(message, error = false) { const toast = $('#toast'); toast.textContent = message; toast.className = `toast show${error ? ' error' : ''}`; clearTimeout(showToast.timer); showToast.timer = setTimeout(() => { toast.className = 'toast'; }, 3500); }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;' }[char])); }
function initials(name) { return (name || '?').split(' ').map(part => part[0]).slice(0, 2).join('').toUpperCase(); }
function formatMoney(paise = 0) { const value = Math.abs(Number(paise) || 0) / 100; return `₹${value.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`; }
function signedMoney(paise) { return `${Number(paise) < 0 ? '−' : Number(paise) > 0 ? '+' : ''}${formatMoney(paise)}`; }
function niceDate(value) { return new Date(`${value}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }); }
function today() { return new Date().toISOString().slice(0, 10); }
function showAuth() { $('#auth-screen').hidden = false; $('#app').hidden = true; }
function showApp() { $('#auth-screen').hidden = true; $('#app').hidden = false; }
function setAuthTab(tab) { $$('.tab').forEach(button => button.classList.toggle('active', button.dataset.authTab === tab)); $('#login-form').hidden = tab !== 'login'; $('#register-form').hidden = tab !== 'register'; }

async function boot() { try { state.user = (await api('/api/me')).user; showApp(); await loadData(); } catch { showAuth(); } }
async function loadData() {
  state.groups = (await api('/api/groups')).groups;
  if (!state.groups.some(group => group.id === state.selectedGroupId)) state.selectedGroupId = state.groups[0]?.id || null;
  if (state.selectedGroupId) state.group = (await api(`/api/groups/${state.selectedGroupId}`)).group;
  else state.group = null;
  renderAll();
}
async function reloadGroup() { if (state.selectedGroupId) state.group = (await api(`/api/groups/${state.selectedGroupId}`)).group; renderAll(); }
function renderAll() {
  $('#user-name').textContent = state.user.name; $('#user-email').textContent = state.user.email; $('#user-avatar').textContent = initials(state.user.name); $('#mobile-avatar').textContent = initials(state.user.name);
  renderGroups(); renderGroup();
}
function renderGroups() {
  $('#group-list').innerHTML = state.groups.length ? state.groups.map(group => `<button class="group-link ${group.id === state.selectedGroupId ? 'active' : ''}" data-action="select-group" data-id="${group.id}"><span class="group-icon">${escapeHtml(group.name.slice(0, 1).toUpperCase())}</span><span><b>${escapeHtml(group.name)}</b><small>${group.member_count} member${group.member_count === 1 ? '' : 's'}</small></span></button>`).join('') : '<p class="side-empty">No groups yet</p>';
}
function renderGroup() {
  const group = state.group;
  $('#page-title').textContent = group ? group.name : 'Your groups';
  $('#page-subtitle').textContent = group ? `${group.members.length} friends · ${group.expenses.length} shared expense${group.expenses.length === 1 ? '' : 's'}` : 'Create a group to start splitting.';
  $$('.top-actions .button').forEach(button => { button.disabled = !group; });
  if (!group) { $('#group-total').textContent = '₹0'; $('#group-caption').textContent = 'Create your first group'; $('#total-spent').textContent = '₹0'; $('#expense-count').textContent = 'No expenses yet'; $('#member-count').textContent = '0'; $('#balance-list').innerHTML = emptyState('Create a group and invite friends.'); $('#settlement-list').innerHTML = emptyState('Your settlement plan will appear here.'); $('#member-list').innerHTML = ''; $('#expense-list').innerHTML = emptyState('Add your first shared expense.'); return; }
  const current = group.members.find(member => member.id === state.user.id)?.net_paise || 0;
  $('#group-total').textContent = signedMoney(current); $('#group-caption').textContent = current > 0 ? 'you are owed in this group' : current < 0 ? 'you owe in this group' : 'you are all settled up';
  $('#total-spent').textContent = formatMoney(group.total_expenses_paise); $('#expense-count').textContent = `${group.expenses.length} expense${group.expenses.length === 1 ? '' : 's'}`; $('#member-count').textContent = group.members.length;
  renderBalances(group); renderSettlements(group); renderMembers(group); renderExpenses(group);
}
function emptyState(text) { return `<div class="empty-mini">${escapeHtml(text)}</div>`; }
function renderBalances(group) {
  $('#balance-list').innerHTML = group.members.map(member => { const mine = member.id === state.user.id; const net = member.net_paise; const message = net > 0 ? (mine ? 'You are owed' : 'gets back') : net < 0 ? (mine ? 'You owe' : 'owes') : 'settled'; return `<div class="balance-row"><span class="avatar small-avatar">${initials(member.name)}</span><div class="balance-person"><strong>${mine ? 'You' : escapeHtml(member.name)}</strong><small>${escapeHtml(message)}</small></div><strong class="balance-value ${net > 0 ? 'positive' : net < 0 ? 'negative' : 'neutral'}">${net ? signedMoney(net) : '—'}</strong></div>`; }).join('');
}
function renderSettlements(group) {
  const plan = group.settlement_plan; $('#payment-count').textContent = plan.length ? `${plan.length} payment${plan.length === 1 ? '' : 's'}` : 'all clear';
  $('#settlement-list').innerHTML = plan.length ? plan.map((payment, index) => `<div class="settle-row"><div class="settle-number">${index + 1}</div><div class="settle-copy"><strong>${payment.from_user_id === state.user.id ? 'You' : escapeHtml(payment.from_name)} <span>→</span> ${payment.to_user_id === state.user.id ? 'You' : escapeHtml(payment.to_name)}</strong><small>${formatMoney(payment.amount_paise)} clears this balance</small></div><button class="settle-button" data-action="record-payment" data-from="${payment.from_user_id}" data-to="${payment.to_user_id}" data-amount="${payment.amount_paise}">Record</button></div>`).join('') : '<div class="settled-state"><span>✓</span><div><strong>Everyone is settled</strong><small>No payments needed right now.</small></div></div>';
}
function renderMembers(group) { $('#member-list').innerHTML = group.members.map(member => `<div class="member-card"><span class="avatar">${initials(member.name)}</span><div><strong>${escapeHtml(member.name)}${member.id === group.created_by ? ' <span class="owner-label">owner</span>' : ''}</strong><small>${escapeHtml(member.email)}</small></div></div>`).join(''); }
function renderExpenses(group) { $('#expense-list').innerHTML = group.expenses.length ? group.expenses.map(expenseTemplate).join('') : emptyState('No expenses yet. Add the first one to get the group moving.'); }
function expenseTemplate(item) { const shareText = item.split_type === 'equal' ? 'equally' : item.split_type === 'exact' ? 'by exact amounts' : 'by percentages'; return `<article class="expense-row"><div class="expense-icon">${escapeHtml(item.description.slice(0, 1).toUpperCase())}</div><div class="expense-main"><strong>${escapeHtml(item.description)}</strong><small>${escapeHtml(item.paid_by_name)} paid · split ${shareText} · ${niceDate(item.expense_date)}</small></div><strong class="expense-amount">${formatMoney(item.amount_paise)}</strong><div class="expense-actions"><button data-action="edit-expense" data-id="${item.id}">Edit</button><button data-action="delete-expense" data-id="${item.id}" aria-label="Delete expense">×</button></div></article>`; }

function modalHeader(title, subtitle = '') { return `<div class="modal-header"><div><h2>${escapeHtml(title)}</h2>${subtitle ? `<p class="muted">${escapeHtml(subtitle)}</p>` : ''}</div><button class="close-modal" data-action="close-modal" aria-label="Close">×</button></div>`; }
function openModal(content, wide = false) { $('#modal').className = `modal${wide ? ' wide' : ''}`; $('#modal').innerHTML = content; $('#modal-layer').hidden = false; }
function closeModal() { $('#modal-layer').hidden = true; }
function memberOptions(selected) { return state.group.members.map(member => `<option value="${member.id}" ${Number(selected) === member.id ? 'selected' : ''}>${escapeHtml(member.name)}${member.id === state.user.id ? ' (you)' : ''}</option>`).join(''); }
function splitInputs(item = null) { const existing = Object.fromEntries((item?.shares || []).map(share => [share.user_id, share.amount_paise])); return state.group.members.map(member => { const value = item?.split_type === 'percentage' ? (existing[member.id] / item.amount_paise * 100).toFixed(2) : item ? (existing[member.id] / 100).toFixed(2) : ''; return `<div class="split-member"><span class="avatar tiny-avatar">${initials(member.name)}</span><span>${escapeHtml(member.name)}</span><input name="split-${member.id}" value="${value}" placeholder="${item?.split_type === 'percentage' ? '0' : '0.00'}" inputmode="decimal"></div>`; }).join(''); }
function openExpense(item = null) {
  const kind = item?.split_type || 'equal';
  openModal(`${modalHeader(item ? 'Edit expense' : 'Add an expense', item ? 'Changing this expense recalculates every balance.' : 'Every split is stored in exact paise.')}<form data-form="expense" ${item ? `data-id="${item.id}"` : ''}><label>What was it for?<input name="description" maxlength="100" value="${escapeHtml(item?.description || '')}" placeholder="Dinner, taxi, hotel…" required></label><div class="form-row"><label>Total amount<input name="amount" value="${item ? (item.amount_paise / 100).toFixed(2) : ''}" placeholder="0.00" inputmode="decimal" required></label><label>Date<input name="date" type="date" value="${item?.expense_date || today()}" required></label></div><label>Paid by<select name="paid_by" required>${memberOptions(item?.paid_by)}</select></label><div class="split-heading"><div><strong>How should it split?</strong><small>Remainders are handled automatically.</small></div><div class="kind-tabs">${['equal', 'exact', 'percentage'].map(type => `<button type="button" class="kind-tab ${kind === type ? 'active' : ''}" data-action="set-split" data-kind="${type}">${type === 'equal' ? 'Equally' : type === 'exact' ? 'Exact' : 'Percent'}</button>`).join('')}</div></div><input type="hidden" name="split_type" id="split-type" value="${kind}"><div id="split-inputs" class="split-inputs">${splitInputs(item)}</div><p id="split-help" class="form-note">Everyone gets an equal share. Any leftover paise goes to the first people in the list.</p><div class="modal-footer"><button type="button" class="button button-secondary" data-action="close-modal">Cancel</button><button class="button button-primary" type="submit">${item ? 'Save changes' : 'Add expense'} <span>→</span></button></div></form>`);
  updateSplitHelp(kind);
}
function updateSplitHelp(kind) { $('#split-type').value = kind; $$('.kind-tab').forEach(button => button.classList.toggle('active', button.dataset.kind === kind)); $('#split-help').textContent = kind === 'equal' ? 'Everyone gets an equal share. Any leftover paise goes to the first people in the list.' : kind === 'exact' ? 'Enter the rupee amount each person consumed. The total must match exactly.' : 'Enter each person’s percentage. Percentages must add up to 100.'; $$('#split-inputs input').forEach(input => { input.placeholder = kind === 'percentage' ? '0' : '0.00'; }); }
function openGroup() { openModal(`${modalHeader('Create a group', 'Start a shared space for a trip, home, or dinner crew.')}<form data-form="group"><label>Group name<input name="name" maxlength="80" placeholder="Goa Weekend" required></label><div class="modal-footer"><button type="button" class="button button-secondary" data-action="close-modal">Cancel</button><button class="button button-primary" type="submit">Create group <span>→</span></button></div></form>`); }
function openMember() { if (!state.group) return showToast('Create a group first', true); openModal(`${modalHeader('Add a member', 'They must already have a registered account.')}<form data-form="member"><label>Member email<input name="email" type="email" placeholder="friend@example.com" required></label><p class="form-note">Demo accounts: arjun@demo.com and zoya@demo.com</p><div class="modal-footer"><button type="button" class="button button-secondary" data-action="close-modal">Cancel</button><button class="button button-primary" type="submit">Add member <span>→</span></button></div></form>`); }
function openPayment(plan = null) { if (!state.group) return; openModal(`${modalHeader('Record a payment', 'This payment will reduce the payer’s balance immediately.')}<form data-form="payment"><div class="form-row"><label>From<select name="payer_id">${memberOptions(plan?.from_user_id)}</select></label><label>To<select name="payee_id">${memberOptions(plan?.to_user_id)}</select></label></div><div class="form-row"><label>Amount<input name="amount" value="${plan ? (plan.amount_paise / 100).toFixed(2) : ''}" placeholder="0.00" inputmode="decimal" required></label><label>Date<input name="date" type="date" value="${today()}" required></label></div><label>Note <span class="muted">(optional)</span><input name="note" maxlength="160" placeholder="Paid via UPI"></label><div class="modal-footer"><button type="button" class="button button-secondary" data-action="close-modal">Cancel</button><button class="button button-primary" type="submit">Record payment <span>→</span></button></div></form>`); }

 document.addEventListener('click', async event => {
  const target = event.target.closest('[data-action], [data-view], [data-auth-tab], [data-demo]'); if (!target) return;
  try {
    if (target.dataset.authTab) { setAuthTab(target.dataset.authTab); return; }
    if (target.dataset.demo) { $('[name=email]', $('#login-form')).value = target.dataset.demo; $('[name=password]', $('#login-form')).value = 'demo123'; return; }
    const action = target.dataset.action;
    if (action === 'close-modal') closeModal();
    else if (action === 'toggle-menu') $('.sidebar').classList.toggle('open');
    else if (action === 'logout') { await api('/api/logout', { method: 'POST' }); state.user = null; showAuth(); }
    else if (action === 'new-group') openGroup();
    else if (action === 'new-member') openMember();
    else if (action === 'new-expense') { if (!state.group) showToast('Create a group first', true); else openExpense(); }
    else if (action === 'select-group') { state.selectedGroupId = Number(target.dataset.id); await loadData(); $('.sidebar').classList.remove('open'); }
    else if (action === 'set-split') { updateSplitHelp(target.dataset.kind); }
    else if (action === 'edit-expense') openExpense(state.group.expenses.find(item => item.id === Number(target.dataset.id)));
    else if (action === 'delete-expense') { if (confirm('Delete this expense? All balances will update immediately.')) { await api(`/api/groups/${state.group.id}/expenses/${target.dataset.id}`, { method: 'DELETE' }); await reloadGroup(); showToast('Expense deleted'); } }
    else if (action === 'record-payment') openPayment({ from_user_id: Number(target.dataset.from), to_user_id: Number(target.dataset.to), amount_paise: Number(target.dataset.amount) });
  } catch (error) { showToast(error.message, true); }
 });

document.addEventListener('submit', async event => {
  const form = event.target; if (!form.dataset.form) return; event.preventDefault();
  try {
    const values = Object.fromEntries(new FormData(form).entries());
    if (form.dataset.form === 'login' || form.dataset.form === 'register') { state.user = (await api(`/api/${form.dataset.form}`, { method: 'POST', body: JSON.stringify(values) })).user; showApp(); await loadData(); showToast(form.dataset.form === 'login' ? 'Welcome back' : 'Account created'); return; }
    if (form.dataset.form === 'group') { const result = await api('/api/groups', { method: 'POST', body: JSON.stringify(values) }); state.selectedGroupId = result.group_id; closeModal(); await loadData(); showToast('Group created'); return; }
    if (form.dataset.form === 'member') { await api(`/api/groups/${state.group.id}/members`, { method: 'POST', body: JSON.stringify(values) }); closeModal(); await loadData(); showToast('Member added'); return; }
    if (form.dataset.form === 'expense') {
      const splitValues = state.group.members.map(member => values[`split-${member.id}`] || '');
      const payload = { description: values.description, amount: values.amount, date: values.date, paid_by: Number(values.paid_by), split_type: values.split_type, split_values: splitValues };
      const path = form.dataset.id ? `/api/groups/${state.group.id}/expenses/${form.dataset.id}/edit` : `/api/groups/${state.group.id}/expenses`;
      await api(path, { method: 'POST', body: JSON.stringify(payload) }); closeModal(); await reloadGroup(); showToast(form.dataset.id ? 'Expense updated' : 'Expense added'); return;
    }
    if (form.dataset.form === 'payment') { await api(`/api/groups/${state.group.id}/payments`, { method: 'POST', body: JSON.stringify({ ...values, payer_id: Number(values.payer_id), payee_id: Number(values.payee_id) }) }); closeModal(); await reloadGroup(); showToast('Payment recorded'); }
  } catch (error) { showToast(error.message, true); }
});
boot();
