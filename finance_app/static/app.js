const state = {
  view: "consolidated",
  company: "all",
  user: null,
  users: [],
  clients: [],
  companies: [],
  goals: null,
  month: "",
  clientMonth: "",
  metadata: { categories: [], cost_centers: [], clients: [], companies: [] },
  transactions: [],
  documents: [],
  ranking: null,
  reviewDocument: null,
  dashboard: null,
  consolidated: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const integer = new Intl.NumberFormat("pt-BR");
const statusLabel = { paid: "Pago/recebido", pending: "Pendente", overdue: "Vencido", cancelled: "Cancelado" };
const colors = ["#2f6fed", "#7657d6", "#d68a18", "#d94d5c", "#4f84c4", "#9a62b4", "#b56a43", "#52657e"];

function formatMoney(cents = 0) { return money.format(Number(cents) / 100); }
function companyName(companyId) {
  return (state.metadata.companies || []).find(item => item.id === Number(companyId))?.name || "Não definida";
}
function formatDate(value) {
  if (!value) return "—";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}
function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}
function toast(message, type = "success") {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show ${type === "error" ? "error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.className = "toast", 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ error: "Falha ao processar a solicitação." }));
    if (response.status === 401 && !path.startsWith("/api/auth/")) showAuth(false);
    throw new Error(payload.error || "Falha ao processar a solicitação.");
  }
  if (response.status === 204) return null;
  return response.json();
}

function queryString(values) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value) query.set(key, value); });
  return query.toString();
}

async function initialize() {
  wireAuthEvents();
  const status = await api("/api/auth/status");
  if (!status.authenticated) {
    showAuth(status.setup_required);
    return;
  }
  await enterApplication(status.user);
}

function wireAuthEvents() {
  $("#loginForm").addEventListener("submit", login);
  $("#setupForm").addEventListener("submit", setupAdministrator);
  $("#logoutButton").addEventListener("click", logout);
}

function showAuth(setupRequired) {
  state.user = null;
  document.body.classList.remove("authenticated");
  $("#setupForm").hidden = !setupRequired;
  $("#loginForm").hidden = setupRequired;
  $("#loginError").textContent = "";
  $("#setupError").textContent = "";
}

async function enterApplication(user) {
  state.user = user;
  state.view = "consolidated";
  state.company = "all";
  document.body.classList.add("authenticated");
  $("#currentUserName").textContent = user.name;
  $("#currentUserRole").textContent = user.role === "admin" ? "Administrador" : "Usuário comum";
  $("#userAvatar").textContent = user.name.trim().charAt(0).toUpperCase() || "U";
  $$(".admin-only").forEach(element => { element.hidden = user.role !== "admin"; });
  $("#pageTitle").textContent = "Consolidado";
  $$(".view").forEach(element => element.classList.toggle("active", element.id === "consolidatedView"));
  $$(".nav-item").forEach(element => element.classList.toggle("active", element.dataset.view === "consolidated"));
  if (!enterApplication.wired) {
    wireEvents();
    enterApplication.wired = true;
  }
  state.metadata = await api("/api/meta");
  const currentMonth = new Date().toISOString().slice(0, 7);
  state.month = state.metadata.latest_month || currentMonth;
  state.clientMonth = state.month;
  $("#monthPicker").value = state.month;
  $("#clientMonthFilter").value = state.clientMonth;
  populateDataLists();
  await loadCurrentView();
}

async function login(event) {
  event.preventDefault();
  const form = event.currentTarget;
  $("#loginError").textContent = "";
  try {
    const payload = Object.fromEntries(new FormData(form));
    const result = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    form.reset();
    await enterApplication(result.user);
  } catch (error) { $("#loginError").textContent = error.message; }
}

async function setupAdministrator(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form));
  $("#setupError").textContent = "";
  if (payload.password !== payload.password_confirmation) {
    $("#setupError").textContent = "A confirmação da senha não corresponde.";
    return;
  }
  delete payload.password_confirmation;
  try {
    const result = await api("/api/auth/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    form.reset();
    await enterApplication(result.user);
    toast("Administrador criado com sucesso.");
  } catch (error) { $("#setupError").textContent = error.message; }
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); }
  finally { showAuth(false); }
}

function populateDataLists() {
  const categoryOptions = state.metadata.categories.map(item => `<option value="${escapeHtml(item)}"></option>`).join("");
  $("#allCategories").innerHTML = categoryOptions;
  $("#expenseCategories").innerHTML = categoryOptions;
  $("#categoryFilter").innerHTML = `<option value="">Todas</option>${state.metadata.categories.map(item => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("")}`;
  $("#costCenters").innerHTML = state.metadata.cost_centers.map(item => `<option value="${escapeHtml(item)}"></option>`).join("");
  const clientSelect = $("#transactionClient");
  const selectedClient = clientSelect.value;
  clientSelect.innerHTML = `<option value="">Informar manualmente</option>${(state.metadata.clients || []).map(item => `<option value="${item.id}">${escapeHtml(item.name)}${item.tax_id ? ` · ${escapeHtml(formatTaxId(item.tax_id))}` : ""}</option>`).join("")}`;
  clientSelect.value = selectedClient;
  const companyOptions = (state.metadata.companies || []).map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
  $("#companyPicker").innerHTML = `<option value="all">Consolidado</option>${companyOptions}`;
  $("#companyPicker").value = state.company;
  $("#transactionCompany").innerHTML = `<option value="">Selecione</option>${companyOptions}`;
}

function wireEvents() {
  $$(".nav-item").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$('[data-go]').forEach(button => button.addEventListener("click", () => switchView(button.dataset.go)));
  $("#menuButton").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#monthPicker").addEventListener("change", async event => { state.month = event.target.value; await loadCurrentView(); });
  $("#companyPicker").addEventListener("change", async event => {
    state.company = event.target.value;
    if (state.view === "consolidated" && state.company !== "all") return switchView("dashboard");
    await loadCurrentView();
  });
  $("#newTransactionButton").addEventListener("click", () => openTransactionDialog());
  $("#transactionForm").addEventListener("submit", saveTransaction);
  $$('#transactionForm input[name="kind"]').forEach(input => input.addEventListener("change", updateTransactionClientVisibility));
  $("#transactionClient").addEventListener("change", applySelectedClient);
  $("#budgetForm").addEventListener("submit", saveBudget);
  $("#importButton").addEventListener("click", () => $("#importInput").click());
  $("#importInput").addEventListener("change", importWorkbook);
  $("#attachDocumentButton").addEventListener("click", () => $("#documentInput").click());
  $("#documentDropZone").addEventListener("click", () => $("#documentInput").click());
  $("#documentInput").addEventListener("change", event => analyzeDocument(event.target.files[0]));
  ["dragenter", "dragover"].forEach(name => $("#documentDropZone").addEventListener(name, event => { event.preventDefault(); $("#documentDropZone").classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(name => $("#documentDropZone").addEventListener(name, event => { event.preventDefault(); $("#documentDropZone").classList.remove("dragging"); }));
  $("#documentDropZone").addEventListener("drop", event => analyzeDocument(event.dataTransfer.files[0]));
  $("#documentReviewForm").addEventListener("submit", saveDocumentTransaction);
  $$('#documentReviewForm input[name="kind"]').forEach(input => input.addEventListener("change", applyReviewParty));
  $("#rankingScope").addEventListener("change", loadRanking);
  $("#rankingSearch").addEventListener("input", () => renderRankingTable(state.ranking));
  $("#newClientButton").addEventListener("click", () => openClientDialog());
  $("#clientForm").addEventListener("submit", saveClient);
  $("#clientStatusFilter").addEventListener("change", loadClients);
  $("#clientMonthFilter").addEventListener("change", event => { state.clientMonth = event.target.value || state.month; loadClients(); });
  $("#clientSearch").addEventListener("input", debounce(loadClients, 300));
  $("#clearClientFilters").addEventListener("click", () => { $("#clientSearch").value = ""; $("#clientStatusFilter").value = ""; state.clientMonth = state.month; $("#clientMonthFilter").value = state.clientMonth; loadClients(); });
  $("#goalForm").addEventListener("submit", saveGoal);
  $("#exportButton").addEventListener("click", exportReport);
  $("#newUserButton").addEventListener("click", () => openUserDialog());
  $("#userForm").addEventListener("submit", saveUser);
  $("#newCompanyButton").addEventListener("click", openCompanyDialog);
  $("#companyForm").addEventListener("submit", saveCompany);
  $$('[data-close-dialog]').forEach(button => button.addEventListener("click", () => $("#" + button.dataset.closeDialog).close()));
  $("#clearFiltersButton").addEventListener("click", clearFilters);
  ["#kindFilter", "#statusFilter", "#categoryFilter"].forEach(selector => $(selector).addEventListener("change", loadTransactions));
  $("#searchFilter").addEventListener("input", debounce(loadTransactions, 300));
  window.addEventListener("resize", debounce(() => {
    if (state.view === "consolidated" && state.consolidated) drawCompanyChart(state.consolidated.items);
    if (state.view === "dashboard" && state.dashboard) renderCharts(state.dashboard);
    if (state.view === "goals" && state.goals) drawGoalsChart(state.goals.history);
  }, 180));
}

function switchView(view) {
  if (["users", "companies"].includes(view) && state.user?.role !== "admin") return;
  if (view === "consolidated") {
    state.company = "all";
    $("#companyPicker").value = "all";
  }
  state.view = view;
  const titles = { consolidated: "Consolidado", dashboard: "Visão geral", transactions: "Lançamentos", clients: "Clientes", documents: "Notas fiscais", ranking: "Ranking de clientes", budgets: "Orçamentos", goals: "Metas e projeções", reports: "Relatórios", companies: "Empresas", users: "Usuários" };
  $("#pageTitle").textContent = titles[view];
  $$(".view").forEach(element => element.classList.remove("active"));
  $(`#${view}View`).classList.add("active");
  $$(".nav-item").forEach(element => element.classList.toggle("active", element.dataset.view === view));
  $("#sidebar").classList.remove("open");
  loadCurrentView().catch(error => toast(error.message, "error"));
}

async function loadCurrentView() {
  if (state.view === "consolidated") return loadConsolidated();
  if (state.view === "dashboard") return loadDashboard();
  if (state.view === "transactions") return loadTransactions();
  if (state.view === "clients") return loadClients();
  if (state.view === "documents") return loadDocuments();
  if (state.view === "ranking") return loadRanking();
  if (state.view === "budgets") return loadBudgets();
  if (state.view === "goals") return loadGoals();
  if (state.view === "reports") return loadReports();
  if (state.view === "companies") return loadCompanies();
  if (state.view === "users") return loadUsers();
}

async function loadConsolidated() {
  const data = await api(`/api/consolidated?month=${state.month}`);
  state.consolidated = data;
  $("#consolidatedRevenue").textContent = formatMoney(data.totals.revenue_cents);
  $("#consolidatedInvoiceCount").textContent = `${integer.format(data.totals.invoice_count)} notas ativas`;
  $("#consolidatedCancelled").textContent = formatMoney(data.totals.cancelled_cents);
  $("#consolidatedCancelledCount").textContent = `${integer.format(data.totals.cancelled_count)} canceladas`;
  $("#consolidatedClients").textContent = integer.format(data.totals.client_count);
  $("#unassignedTransactions").textContent = integer.format(data.totals.unassigned_count);
  $("#companyComparisonCards").innerHTML = data.items.map(item => {
    const gross = item.revenue_cents + item.cancelled_cents;
    const cancellation = gross ? item.cancelled_cents / gross * 100 : 0;
    return `<article class="company-card">
      <header><div><span>Empresa</span><h3>${escapeHtml(item.name)}</h3><small>${escapeHtml(item.municipality || "")}</small></div><strong>${item.share_percent.toFixed(1).replace(".", ",")}%</strong></header>
      <div class="company-card-metrics"><div><span>Receita</span><strong>${formatMoney(item.revenue_cents)}</strong></div><div><span>Notas</span><strong>${integer.format(item.invoice_count)}</strong></div><div><span>Clientes</span><strong>${integer.format(item.client_count)}</strong></div><div><span>Cancelamento</span><strong>${cancellation.toFixed(1).replace(".", ",")}%</strong></div></div>
      <button class="button secondary full" data-open-company="${item.id}">Ver somente ${escapeHtml(item.name)}</button>
    </article>`;
  }).join("");
  $$('[data-open-company]').forEach(button => button.addEventListener("click", () => {
    state.company = button.dataset.openCompany;
    $("#companyPicker").value = state.company;
    switchView("dashboard");
  }));
  requestAnimationFrame(() => drawCompanyChart(data.items));
}

function drawCompanyChart(items) {
  const canvas = $("#companyChart");
  const { context, width, height } = prepareCanvas(canvas, 260);
  context.clearRect(0, 0, width, height);
  const padding = { top: 20, right: 16, bottom: 44, left: 58 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maximum = Math.max(...items.flatMap(item => [item.revenue_cents, item.cancelled_cents]), 1);
  context.font = "10px Inter, sans-serif";
  for (let step = 0; step <= 4; step++) {
    const y = padding.top + plotHeight / 4 * step;
    context.strokeStyle = "#edf0f4"; context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
    context.fillStyle = "#8993a4"; context.textAlign = "right"; context.fillText(`${Math.round((maximum - maximum / 4 * step) / 100000)}k`, padding.left - 8, y + 3);
  }
  const group = plotWidth / Math.max(items.length, 1);
  items.forEach((item, index) => {
    const center = padding.left + group * index + group / 2;
    const barWidth = Math.min(42, group * .22);
    [[item.revenue_cents, "#2f6fed", -barWidth - 4], [item.cancelled_cents, "#d94d5c", 4]].forEach(([value, color, offset]) => {
      const barHeight = value / maximum * plotHeight;
      context.fillStyle = color; context.beginPath(); context.roundRect(center + offset, padding.top + plotHeight - barHeight, barWidth, barHeight, 4); context.fill();
    });
    context.fillStyle = "#52657e"; context.textAlign = "center"; context.fillText(item.name, center, height - 16);
  });
}

async function loadDashboard() {
  const data = await api(`/api/dashboard?${queryString({ month: state.month, company: state.company })}`);
  state.dashboard = data;
  const totals = data.totals;
  $("#incomeMetric").textContent = formatMoney(totals.income_cents);
  $("#expenseMetric").textContent = formatMoney(totals.expense_cents);
  $("#balanceMetric").textContent = formatMoney(totals.balance_cents);
  $("#payableMetric").textContent = formatMoney(totals.payable_cents);
  $("#incomeCount").textContent = `${integer.format(totals.income_count)} lançamento${totals.income_count === 1 ? "" : "s"}`;
  $("#expenseCount").textContent = `${integer.format(totals.expense_count)} lançamento${totals.expense_count === 1 ? "" : "s"}`;
  $("#overdueCount").textContent = `${integer.format(totals.overdue_count)} conta${totals.overdue_count === 1 ? "" : "s"} vencida${totals.overdue_count === 1 ? "" : "s"}`;
  renderRecent(data.recent);
  renderBudgetSummary(data);
  requestAnimationFrame(() => renderCharts(data));
}

function renderRecent(rows) {
  $("#recentTable").innerHTML = rows.length ? rows.map(row => `
    <tr>
      <td class="transaction-name"><strong>${escapeHtml(row.description)}</strong><small>${escapeHtml(row.category)}</small></td>
      <td>${formatDate(row.transaction_date)}</td>
      <td><span class="badge ${row.status}">${statusLabel[row.status]}</span></td>
      <td class="align-right"><strong class="amount-${row.kind}">${row.kind === "expense" ? "−" : "+"} ${formatMoney(row.amount_cents)}</strong></td>
    </tr>`).join("") : `<tr><td colspan="4" class="empty-state">Nenhum lançamento no período.</td></tr>`;
}

function renderBudgetSummary(data) {
  const limit = data.budget_limit_cents;
  const spent = data.totals.expense_cents;
  const usage = limit ? Math.round(spent / limit * 100) : 0;
  $("#budgetUsage").textContent = `${usage}%`;
  $("#budgetSpent").textContent = formatMoney(spent);
  $("#budgetLimit").textContent = formatMoney(limit);
  $("#budgetRing").style.setProperty("--usage", `${Math.min(usage, 100) * 3.6}deg`);
  $("#budgetMessage").textContent = !limit
    ? "Defina limites por categoria para acompanhar o orçamento."
    : usage > 100 ? `O orçamento foi ultrapassado em ${formatMoney(spent - limit)}.`
    : `Ainda há ${formatMoney(limit - spent)} disponível para o período.`;
}

function renderCharts(data) {
  drawFlowChart($("#flowChart"), data.daily_flow);
  drawCategoryChart($("#categoryChart"), data.expenses_by_category);
  const total = data.expenses_by_category.reduce((sum, item) => sum + item.amount_cents, 0);
  $("#categoryTotal").textContent = formatMoney(total).replace(",00", "");
  $("#categoryLegend").classList.toggle("empty-state", !data.expenses_by_category.length);
  $("#categoryLegend").innerHTML = data.expenses_by_category.length
    ? data.expenses_by_category.slice(0, 6).map((item, index) => `<div class="category-row"><i style="background:${colors[index % colors.length]}"></i><span>${escapeHtml(item.category)}</span><strong>${formatMoney(item.amount_cents)}</strong></div>`).join("")
    : "Cadastre despesas para visualizar.";
}

function prepareCanvas(canvas, height) {
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(canvas.clientWidth, 240);
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width, height };
}

function drawFlowChart(canvas, rows) {
  const { context, width, height } = prepareCanvas(canvas, 250);
  context.clearRect(0, 0, width, height);
  const padding = { top: 16, right: 10, bottom: 30, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maximum = Math.max(...rows.flatMap(row => [row.income_cents, row.expense_cents]), 1);
  context.font = "10px Inter, sans-serif";
  context.textAlign = "right";
  context.fillStyle = "#8993a4";
  context.strokeStyle = "#edf0f4";
  for (let step = 0; step <= 4; step++) {
    const y = padding.top + plotHeight / 4 * step;
    context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
    const value = maximum - maximum / 4 * step;
    context.fillText(value >= 100000 ? `${(value / 100000).toFixed(0)}k` : `${(value / 100).toFixed(0)}`, padding.left - 8, y + 3);
  }
  if (!rows.length) {
    context.textAlign = "center"; context.fillStyle = "#8c95a5"; context.fillText("Sem movimentações no período", width / 2, height / 2); return;
  }
  const groupWidth = plotWidth / rows.length;
  const barWidth = Math.max(3, Math.min(12, groupWidth * .28));
  rows.forEach((row, index) => {
    const center = padding.left + groupWidth * index + groupWidth / 2;
    [[row.income_cents, "#2f6fed", -barWidth - 1], [row.expense_cents, "#d94d5c", 1]].forEach(([value, color, offset]) => {
      const barHeight = value / maximum * plotHeight;
      context.fillStyle = color;
      context.beginPath(); context.roundRect(center + offset, padding.top + plotHeight - barHeight, barWidth, barHeight, 3); context.fill();
    });
    if (rows.length <= 12 || index % Math.ceil(rows.length / 8) === 0) {
      context.fillStyle = "#8993a4"; context.textAlign = "center"; context.fillText(row.transaction_date.slice(-2), center, height - 9);
    }
  });
}

function drawCategoryChart(canvas, rows) {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = 190 * ratio; canvas.height = 190 * ratio;
  const context = canvas.getContext("2d"); context.setTransform(ratio, 0, 0, ratio, 0, 0); context.clearRect(0, 0, 190, 190);
  const total = rows.reduce((sum, item) => sum + item.amount_cents, 0);
  if (!total) { context.strokeStyle = "#edf0f5"; context.lineWidth = 18; context.beginPath(); context.arc(95, 95, 69, 0, Math.PI * 2); context.stroke(); return; }
  let start = -Math.PI / 2;
  rows.forEach((item, index) => {
    const angle = item.amount_cents / total * Math.PI * 2;
    context.strokeStyle = colors[index % colors.length]; context.lineWidth = 18; context.lineCap = "butt";
    context.beginPath(); context.arc(95, 95, 69, start, start + angle); context.stroke(); start += angle;
  });
}

function transactionFilters() {
  return { month: state.month, company: state.company, kind: $("#kindFilter").value, status: $("#statusFilter").value, category: $("#categoryFilter").value, search: $("#searchFilter").value.trim() };
}

async function loadTransactions() {
  const rows = await api(`/api/transactions?${queryString(transactionFilters())}`);
  state.transactions = rows;
  $("#transactionsTable").innerHTML = rows.length ? rows.map(row => `
    <tr>
      <td>${formatDate(row.transaction_date)}</td>
      <td><span class="company-chip">${escapeHtml(companyName(row.company_id))}</span></td>
      <td class="transaction-name"><strong>${escapeHtml(row.description)}</strong><small>${escapeHtml(row.counterparty || row.cost_center || "Sem favorecido")}</small></td>
      <td>${escapeHtml(row.category)}</td>
      <td><span class="badge ${row.status}">${statusLabel[row.status]}</span></td>
      <td class="align-right"><strong class="amount-${row.kind}">${row.kind === "expense" ? "−" : "+"} ${formatMoney(row.amount_cents)}</strong></td>
      <td><div class="row-actions"><button data-edit="${row.id}" title="Editar lançamento">Editar</button><button class="delete" data-delete="${row.id}" title="Excluir lançamento definitivamente">Excluir</button></div></td>
    </tr>`).join("") : `<tr><td colspan="7" class="empty-state">Nenhum lançamento encontrado.</td></tr>`;
  $("#transactionsCount").textContent = `${integer.format(rows.length)} lançamento${rows.length === 1 ? "" : "s"}`;
  const net = rows.reduce((sum, row) => sum + (row.kind === "income" ? row.amount_cents : -row.amount_cents), 0);
  $("#transactionsTotal").textContent = `Saldo listado: ${formatMoney(net)}`;
  $$('[data-edit]').forEach(button => button.addEventListener("click", () => openTransactionDialog(Number(button.dataset.edit))));
  $$('[data-delete]').forEach(button => button.addEventListener("click", () => deleteTransaction(Number(button.dataset.delete))));
}

function clearFilters() {
  ["#searchFilter", "#kindFilter", "#statusFilter", "#categoryFilter"].forEach(selector => $(selector).value = "");
  loadTransactions().catch(error => toast(error.message, "error"));
}

function openTransactionDialog(id = null) {
  const form = $("#transactionForm");
  form.reset(); $("#formError").textContent = "";
  form.elements.id.value = id || "";
  form.elements.transaction_date.value = new Date().toISOString().slice(0, 10);
  form.elements.status.value = "paid";
  form.elements.kind.value = "expense";
  form.elements.company_id.value = state.company === "all" ? "" : state.company;
  $("#dialogTitle").textContent = id ? "Editar lançamento" : "Novo lançamento";
  if (id) {
    const row = state.transactions.find(item => item.id === id);
    if (!row) return;
    Object.entries(row).forEach(([key, value]) => { if (form.elements[key] && value !== null) form.elements[key].value = value; });
    form.elements.amount.value = (row.amount_cents / 100).toFixed(2).replace(".", ",");
  }
  updateTransactionClientVisibility();
  $("#transactionDialog").showModal();
}

function updateTransactionClientVisibility() {
  const form = $("#transactionForm");
  const isIncome = form.elements.kind.value === "income";
  $(".income-client-field").hidden = !isIncome;
  if (!isIncome) form.elements.client_id.value = "";
}

function applySelectedClient() {
  const form = $("#transactionForm");
  const client = (state.metadata.clients || []).find(item => item.id === Number(form.elements.client_id.value));
  if (!client) return;
  form.elements.counterparty.value = client.name;
  form.elements.counterparty_tax_id.value = formatTaxId(client.tax_id);
}

async function saveTransaction(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submitter = event.submitter;
  if (submitter?.value === "cancel") { $("#transactionDialog").close(); return; }
  const payload = Object.fromEntries(new FormData(form));
  const id = payload.id; delete payload.id;
  try {
    await api(id ? `/api/transactions/${id}` : "/api/transactions", { method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    $("#transactionDialog").close(); toast(id ? "Lançamento atualizado." : "Lançamento criado.");
    state.metadata = await api("/api/meta"); populateDataLists(); await loadCurrentView();
  } catch (error) { $("#formError").textContent = error.message; }
}

async function deleteTransaction(id) {
  if (!window.confirm("Excluir este lançamento? Esta ação não pode ser desfeita.")) return;
  try { await api(`/api/transactions/${id}`, { method: "DELETE" }); toast("Lançamento excluído."); await loadTransactions(); }
  catch (error) { toast(error.message, "error"); }
}

async function loadClients() {
  const selectedMonth = $("#clientMonthFilter").value || state.clientMonth || state.month;
  state.clientMonth = selectedMonth;
  const params = queryString({ month: selectedMonth, company: state.company, search: $("#clientSearch").value.trim(), status: $("#clientStatusFilter").value });
  const data = await api(`/api/clients?${params}`);
  state.clients = data.items;
  $("#activeClientsMetric").textContent = integer.format(data.summary.active_count);
  $("#inactiveClientsMetric").textContent = integer.format(data.summary.inactive_count);
  $("#newClientsMetric").textContent = integer.format(data.summary.new_count);
  $("#clientRevenueMetric").textContent = formatMoney(data.summary.revenue_cents);
  $("#clientsTable").innerHTML = data.items.length ? data.items.map(client => `
    <tr>
      <td class="transaction-name"><strong>${escapeHtml(client.name)}</strong><small>${client.tax_id ? escapeHtml(formatTaxId(client.tax_id)) : "Sem CPF/CNPJ"}</small></td>
      <td class="transaction-name"><strong>${escapeHtml(client.contact_name || "—")}</strong><small>${escapeHtml(client.email || client.phone || "Sem contato informado")}</small></td>
      <td><span class="badge ${client.status}">${client.status === "active" ? "Ativo" : "Inativo"}</span></td>
      <td><strong>${formatMoney(client.month_revenue_cents)}</strong><small class="table-note">${integer.format(client.month_invoice_count)} lançamento(s)</small></td>
      <td><strong>${formatMoney(client.total_revenue_cents)}</strong><small class="table-note">${integer.format(client.invoice_count)} lançamento(s)</small></td>
      <td>${formatDate(client.last_revenue_date)}</td>
      <td><div class="row-actions"><button data-edit-client="${client.id}">Editar</button></div></td>
    </tr>`).join("") : `<tr><td colspan="7" class="empty-state">Nenhum cliente encontrado.</td></tr>`;
  $$('[data-edit-client]').forEach(button => button.addEventListener("click", () => openClientDialog(Number(button.dataset.editClient))));
}

async function loadCompanies() {
  const companies = await api("/api/companies");
  state.companies = companies;
  $("#companiesTable").innerHTML = companies.length ? companies.map(company => `
    <tr>
      <td class="transaction-name"><strong>${escapeHtml(company.name)}</strong><small>Código interno: ${company.id}</small></td>
      <td>${escapeHtml(company.municipality || "Não informado")}</td>
      <td><span class="badge active">Disponível no consolidado</span></td>
    </tr>`).join("") : `<tr><td colspan="3" class="empty-state">Nenhuma empresa cadastrada.</td></tr>`;
}

function openCompanyDialog() {
  $("#companyForm").reset();
  $("#companyFormError").textContent = "";
  $("#companyDialog").showModal();
}

async function saveCompany(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form));
  try {
    await api("/api/companies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#companyDialog").close();
    toast("Empresa cadastrada e adicionada ao consolidado.");
    state.metadata = await api("/api/meta");
    populateDataLists();
    await loadCompanies();
  } catch (error) { $("#companyFormError").textContent = error.message; }
}

function openClientDialog(id = null) {
  const form = $("#clientForm");
  form.reset();
  $("#clientFormError").textContent = "";
  form.elements.id.value = id || "";
  form.elements.status.value = "active";
  $("#clientDialogTitle").textContent = id ? "Editar cliente" : "Novo cliente";
  if (id) {
    const client = state.clients.find(item => item.id === id);
    if (!client) return;
    Object.entries(client).forEach(([key, value]) => { if (form.elements[key] && value !== null) form.elements[key].value = value; });
    form.elements.tax_id.value = formatTaxId(client.tax_id);
  }
  $("#clientDialog").showModal();
}

async function saveClient(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { $("#clientDialog").close(); return; }
  const payload = Object.fromEntries(new FormData(event.currentTarget));
  const id = payload.id; delete payload.id;
  try {
    await api(id ? `/api/clients/${id}` : "/api/clients", { method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    $("#clientDialog").close();
    toast(id ? "Cliente atualizado." : "Cliente cadastrado.");
    state.metadata = await api("/api/meta"); populateDataLists(); await loadClients();
  } catch (error) { $("#clientFormError").textContent = error.message; }
}

async function loadGoals() {
  const data = await api(`/api/goals?${queryString({ month: state.month, company: state.company })}`);
  state.goals = data;
  const form = $("#goalForm");
  const goal = data.goal;
  form.elements.revenue_target.value = goal ? (goal.revenue_target_cents / 100).toFixed(2).replace(".", ",") : "";
  form.elements.expense_limit.value = goal ? (goal.expense_limit_cents / 100).toFixed(2).replace(".", ",") : "";
  form.elements.new_clients_target.value = goal?.new_clients_target ?? 0;
  form.elements.notes.value = goal?.notes || "";
  $("#goalRevenueActual").textContent = formatMoney(data.actual.revenue_cents);
  $("#goalExpenseActual").textContent = formatMoney(data.actual.expense_cents);
  $("#goalRevenueProjection").textContent = formatMoney(data.projection.revenue_cents);
  $("#goalResultProjection").textContent = formatMoney(data.projection.result_cents);
  $("#goalRevenueProgress").textContent = goal ? `${data.progress.revenue_percent}% da meta · faltam ${formatMoney(data.progress.revenue_remaining_cents)}` : "Sem meta cadastrada";
  $("#goalExpenseProgress").textContent = goal ? `${data.progress.expense_percent}% do limite · saldo ${formatMoney(data.progress.expense_available_cents)}` : "Sem limite cadastrado";
  $("#goalStatusLabel").textContent = goal ? `Metas cadastradas para ${state.month}` : `Sem metas para ${state.month}`;
  const entries = [
    ["Receita", data.actual.revenue_cents, goal?.revenue_target_cents || 0, data.progress.revenue_percent, "blue"],
    ["Despesas", data.actual.expense_cents, goal?.expense_limit_cents || 0, data.progress.expense_percent, data.progress.expense_percent > 100 ? "danger" : "amber"],
    ["Novos clientes", data.actual.new_clients, goal?.new_clients_target || 0, data.progress.clients_percent, "violet"],
  ];
  $("#goalProgressList").innerHTML = entries.map(([label, actual, target, percent, color], index) => `
    <div class="goal-progress-item"><div><strong>${label}</strong><span>${index < 2 ? `${formatMoney(actual)} de ${target ? formatMoney(target) : "meta não definida"}` : `${integer.format(actual)} de ${target || "meta não definida"}`}</span></div><div class="progress"><i class="${color}" style="width:${Math.min(percent, 100)}%"></i></div><b>${percent}%</b></div>`).join("");
  $("#goalScenariosTable").innerHTML = data.scenarios.map(item => `
    <tr><td><strong>${escapeHtml(item.label)}</strong></td><td>${formatMoney(item.monthly_revenue_cents)}</td><td>${formatMoney(item.annual_revenue_cents)}</td><td>${integer.format(item.notes_month)}</td><td>${integer.format(item.notes_year)}</td><td>${formatMoney(item.average_ticket_cents)}</td></tr>`).join("");
  $("#goalForecastTable").innerHTML = data.forecast_months.map(item => `
    <tr><td><strong>${escapeHtml(item.label)}</strong></td><td>${integer.format(item.estimated_notes)}</td><td>${escapeHtml(item.seasonality)}</td><td>${item.factor.toFixed(2).replace(".", ",")}</td><td>${formatMoney(item.revenue_cents)}</td><td>${formatMoney(item.accumulated_cents)}</td></tr>`).join("");
  requestAnimationFrame(() => drawGoalsChart(data.history));
}

async function saveGoal(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.currentTarget));
  payload.month = state.month;
  payload.company = state.company;
  try {
    state.goals = await api("/api/goals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    toast("Metas salvas para o período."); await loadGoals();
  } catch (error) { toast(error.message, "error"); }
}

function drawGoalsChart(rows) {
  const canvas = $("#goalsChart");
  const { context, width, height } = prepareCanvas(canvas, 240);
  context.clearRect(0, 0, width, height);
  const padding = { top: 18, right: 12, bottom: 34, left: 52 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maximum = Math.max(...rows.flatMap(row => [row.revenue_cents, row.expense_cents, row.goal?.revenue_target_cents || 0]), 1);
  context.font = "10px Inter, sans-serif";
  for (let step = 0; step <= 4; step++) {
    const y = padding.top + plotHeight / 4 * step;
    context.strokeStyle = "#edf0f4"; context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
    context.fillStyle = "#8993a4"; context.textAlign = "right"; context.fillText(`${Math.round((maximum - maximum / 4 * step) / 100000)}k`, padding.left - 8, y + 3);
  }
  const group = plotWidth / rows.length;
  rows.forEach((row, index) => {
    const center = padding.left + group * index + group / 2;
    [[row.revenue_cents, "#2f6fed", -10], [row.expense_cents, "#d94d5c", 2]].forEach(([value, color, offset]) => {
      const barHeight = value / maximum * plotHeight; context.fillStyle = color; context.fillRect(center + offset, padding.top + plotHeight - barHeight, 8, barHeight);
    });
    context.fillStyle = "#8993a4"; context.textAlign = "center"; context.fillText(row.month.slice(5), center, height - 10);
  });
}

async function loadBudgets() {
  const rows = await api(`/api/budgets?${queryString({ month: state.month, company: state.company })}`);
  $("#budgetCards").innerHTML = rows.length ? rows.map(row => {
    const usage = row.limit_cents ? Math.round(row.used_cents / row.limit_cents * 100) : 0;
    const level = usage > 100 ? "danger" : usage >= 80 ? "warning" : "";
    return `<article class="budget-card"><header><h3>${escapeHtml(row.category)}</h3><span>${usage}% utilizado</span></header><div class="progress"><i class="${level}" style="width:${Math.min(usage, 100)}%"></i></div><footer><span>Gasto <strong>${formatMoney(row.used_cents)}</strong></span><span>Limite <strong>${formatMoney(row.limit_cents)}</strong></span></footer></article>`;
  }).join("") : `<article class="panel empty-state">Nenhum orçamento definido para este período.</article>`;
}

async function loadDocuments() {
  const documents = await api(`/api/documents?${queryString({ company: state.company })}`);
  state.documents = documents;
  $("#documentCount").textContent = `${integer.format(documents.length)} documento${documents.length === 1 ? "" : "s"}`;
  const documentStatus = { analyzed: "Analisado", needs_review: "Revisar", linked: "Vinculado" };
  $("#documentsTable").innerHTML = documents.length ? documents.map(document => `
    <tr>
      <td><span class="company-chip">${escapeHtml(companyName(document.company_id))}</span></td>
      <td class="document-file"><strong>${escapeHtml(document.file_name)}</strong><small>${escapeHtml(document.document_type)}${document.document_number ? ` · Nº ${escapeHtml(document.document_number)}` : ""}</small></td>
      <td>${escapeHtml(document.issuer_name || "Não identificado")}</td>
      <td>${formatDate(document.issue_date)}</td>
      <td><span class="badge ${document.extraction_status}">${documentStatus[document.extraction_status]}</span></td>
      <td class="align-right"><strong>${document.total_cents ? formatMoney(document.total_cents) : "—"}</strong></td>
      <td><div class="document-actions"><a href="/api/documents/${document.id}/file" target="_blank" rel="noopener">Abrir</a>${document.transaction_id ? "" : `<button data-review-document="${document.id}">Conferir</button>`}</div></td>
    </tr>`).join("") : `<tr><td colspan="7" class="empty-state">Nenhum documento anexado.</td></tr>`;
  $$('[data-review-document]').forEach(button => button.addEventListener("click", () => {
    const document = state.documents.find(item => item.id === Number(button.dataset.reviewDocument));
    if (document) openDocumentReview(document);
  }));
}

async function analyzeDocument(file) {
  if (!file) return;
  if (state.company === "all") { toast("Selecione Bolotti Reis ou WBK antes de anexar a nota.", "error"); $("#documentInput").value = ""; return; }
  if (file.size > 15 * 1024 * 1024) { toast("O arquivo excede o limite de 15 MB.", "error"); return; }
  toast("Lendo e conferindo a nota fiscal...");
  try {
    const document = await api("/api/documents/analyze", {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name), "X-Company-Id": state.company },
      body: await file.arrayBuffer(),
    });
    state.documents.unshift(document);
    openDocumentReview(document);
    if (state.view === "documents") await loadDocuments();
  } catch (error) { toast(error.message, "error"); }
  finally { $("#documentInput").value = ""; }
}

function formatTaxId(value) {
  if (!value) return "";
  const digits = String(value).replace(/\D/g, "");
  if (digits.length === 14) return digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5");
  if (digits.length === 11) return digits.replace(/^(\d{3})(\d{3})(\d{3})(\d{2})$/, "$1.$2.$3-$4");
  return digits;
}

function openDocumentReview(document) {
  const form = $("#documentReviewForm");
  state.reviewDocument = document;
  form.reset(); $("#documentFormError").textContent = "";
  form.elements.document_id.value = document.id;
  form.elements.kind.value = "expense";
  form.elements.amount.value = document.total_cents ? (document.total_cents / 100).toFixed(2).replace(".", ",") : "";
  form.elements.transaction_date.value = document.issue_date || new Date().toISOString().slice(0, 10);
  form.elements.status.value = "pending";
  form.elements.category.value = "Outros";
  form.elements.document_number.value = document.document_number || "";
  applyReviewParty();
  $("#reviewFileName").textContent = document.file_name;
  $("#documentTypeLabel").textContent = document.document_type;
  $("#reviewCompanyName").textContent = companyName(document.company_id);
  $("#reviewDocumentLink").href = `/api/documents/${document.id}/file`;
  $("#issuerSummary").textContent = document.issuer_name || "Não identificado";
  $("#issuerTaxSummary").textContent = formatTaxId(document.issuer_tax_id);
  $("#recipientSummary").textContent = document.recipient_name || "Não identificado";
  $("#recipientTaxSummary").textContent = formatTaxId(document.recipient_tax_id);
  const confidence = Math.round((document.confidence || 0) * 100);
  $("#confidenceBadge").textContent = `${confidence}% de confiança`;
  $("#confidenceBadge").classList.toggle("warning", confidence < 85);
  $("#documentWarnings").innerHTML = (document.extracted?.warnings || []).map(message => `<div class="document-warning">⚠ ${escapeHtml(message)}</div>`).join("");
  $("#documentReviewDialog").showModal();
}

function applyReviewParty() {
  const document = state.reviewDocument;
  if (!document) return;
  const form = $("#documentReviewForm");
  const kind = form.elements.kind.value;
  const isIncome = kind === "income";
  const partyName = isIncome ? (document.recipient_name || document.issuer_name) : document.issuer_name;
  const partyTaxId = isIncome ? (document.recipient_tax_id || document.issuer_tax_id) : document.issuer_tax_id;
  form.elements.counterparty.value = partyName || "";
  form.elements.counterparty_tax_id.value = formatTaxId(partyTaxId);
  form.elements.description.value = `${document.document_type}${document.document_number ? ` ${document.document_number}` : ""}${partyName ? ` — ${partyName}` : ""}`;
  if (isIncome && form.elements.category.value === "Outros") form.elements.category.value = "Honorários e serviços";
  if (!isIncome && form.elements.category.value === "Honorários e serviços") form.elements.category.value = "Outros";
  const notes = [
    partyTaxId ? `CNPJ/CPF cliente: ${formatTaxId(partyTaxId)}` : "",
    document.access_key ? `Chave/código: ${document.access_key}` : "",
    `Documento fiscal anexado #${document.id}`,
  ].filter(Boolean);
  form.elements.notes.value = notes.join(" | ");
}

async function saveDocumentTransaction(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (event.submitter?.value === "cancel") { $("#documentReviewDialog").close(); return; }
  const payload = Object.fromEntries(new FormData(form));
  const documentId = payload.document_id; delete payload.document_id;
  try {
    await api(`/api/documents/${documentId}/post`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    $("#documentReviewDialog").close();
    toast("Nota conferida e lançamento criado com sucesso.");
    state.metadata = await api("/api/meta"); populateDataLists();
    if (payload.kind === "income") {
      toast("Receita salva e ranking de clientes atualizado.");
      switchView("ranking");
    } else {
      await loadDocuments();
    }
  } catch (error) { $("#documentFormError").textContent = error.message; }
}

async function loadRanking() {
  const scope = $("#rankingScope").value;
  const data = await api(`/api/ranking?${queryString({ month: state.month, scope, company: state.company })}`);
  state.ranking = data;
  const summary = data.summary;
  $("#rankingRevenue").textContent = formatMoney(summary.total_cents);
  $("#rankingInvoiceCount").textContent = `${integer.format(summary.invoice_count)} nota${summary.invoice_count === 1 ? "" : "s"} ativa${summary.invoice_count === 1 ? "" : "s"}`;
  $("#rankingClientCount").textContent = integer.format(summary.client_count);
  $("#rankingTop3").textContent = `${summary.top3_percent.toFixed(2).replace(".", ",")}%`;
  $("#rankingTop3Value").textContent = formatMoney(summary.top3_cents);
  $("#rankingAverage").textContent = formatMoney(summary.average_ticket_cents);
  renderRankingPodium(data.items.slice(0, 3));
  renderRankingTable(data);
}

function renderRankingPodium(items) {
  const classes = ["first", "second", "third"];
  $("#rankingPodium").innerHTML = items.length ? items.map((item, index) => `
    <article class="podium-card ${classes[index]}">
      <header><span class="podium-position">${item.position}º</span><span>${item.share_percent.toFixed(2).replace(".", ",")}% do total</span></header>
      <h3 title="${escapeHtml(item.client_name)}">${escapeHtml(item.client_name)}</h3>
      <span>${formatTaxId(item.tax_id) || "Sem CNPJ/CPF informado"}</span>
      <footer><strong>${formatMoney(item.total_cents)}</strong><span>${item.invoice_count} nota${item.invoice_count === 1 ? "" : "s"}</span></footer>
    </article>`).join("") : `<article class="panel empty-state">Nenhuma receita ativa encontrada para montar o ranking.</article>`;
}

function renderRankingTable(data) {
  if (!data) return;
  const plainSearch = $("#rankingSearch").value.trim().toLocaleLowerCase("pt-BR");
  const digitSearch = plainSearch.replace(/\D/g, "");
  const items = data.items.filter(item => {
    if (!plainSearch) return true;
    return item.client_name.toLocaleLowerCase("pt-BR").includes(plainSearch)
      || (digitSearch && String(item.tax_id || "").includes(digitSearch));
  });
  $("#rankingTable").innerHTML = items.length ? items.map(item => `
    <tr>
      <td><span class="rank-number">${item.position}</span></td>
      <td>${formatTaxId(item.tax_id) || "—"}</td>
      <td class="ranking-client"><strong>${escapeHtml(item.client_name)}</strong><small>Posição calculada pelo faturamento ativo</small></td>
      <td>${integer.format(item.invoice_count)}</td>
      <td class="align-right"><strong>${formatMoney(item.total_cents)}</strong></td>
      <td class="align-right">${item.share_percent.toFixed(2).replace(".", ",")}%</td>
      <td class="align-right">${formatMoney(item.average_ticket_cents)}</td>
    </tr>`).join("") : `<tr><td colspan="7" class="empty-state">Nenhum cliente encontrado.</td></tr>`;
  $("#rankingTableFooter").innerHTML = `<tr><td colspan="3">TOTAL GERAL</td><td>${integer.format(data.summary.invoice_count)}</td><td class="align-right">${formatMoney(data.summary.total_cents)}</td><td class="align-right">100,00%</td><td class="align-right">${formatMoney(data.summary.average_ticket_cents)}</td></tr>`;
}

async function saveBudget(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.currentTarget)); payload.month = state.month; payload.company = state.company;
  try { await api("/api/budgets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); event.currentTarget.reset(); toast("Orçamento salvo."); await loadBudgets(); }
  catch (error) { toast(error.message, "error"); }
}

async function loadReports() {
  const data = await api(`/api/dashboard?${queryString({ month: state.month, company: state.company })}`);
  const totals = data.totals;
  const margin = totals.income_cents ? totals.balance_cents / totals.income_cents * 100 : 0;
  const ticket = totals.income_count ? totals.income_cents / totals.income_count : 0;
  const cancellationRate = totals.income_cents + totals.cancelled_cents ? totals.cancelled_cents / (totals.income_cents + totals.cancelled_cents) * 100 : 0;
  $("#reportMetrics").innerHTML = [
    ["Faturamento líquido", formatMoney(totals.income_cents), `${totals.income_count} receitas realizadas`, "income"],
    ["Despesas pagas", formatMoney(totals.expense_cents), `${totals.expense_count} despesas realizadas`, "expense"],
    ["Resultado", formatMoney(totals.balance_cents), `${margin.toFixed(1).replace(".", ",")}% sobre a receita`, "balance"],
    ["Compromissos", formatMoney(totals.payable_cents), `${totals.overdue_count} contas vencidas`, "payable"],
  ].map(([label, value, helper, type]) => `<article class="metric-card ${type}"><div><span>${label}</span><strong>${value}</strong><small>${helper}</small></div></article>`).join("");
  const mainCategory = data.expenses_by_category[0];
  $("#reportInsights").innerHTML = `
    <div class="insight"><span>Ticket médio de receitas</span><strong>${formatMoney(ticket)}</strong><small>Média dos recebimentos realizados no período.</small></div>
    <div class="insight"><span>Índice de cancelamento</span><strong>${cancellationRate.toFixed(1).replace(".", ",")}%</strong><small>${formatMoney(totals.cancelled_cents)} em lançamentos cancelados.</small></div>
    <div class="insight"><span>Maior grupo de despesas</span><strong>${escapeHtml(mainCategory?.category || "Sem despesas")}</strong><small>${mainCategory ? formatMoney(mainCategory.amount_cents) : "Cadastre os gastos do período."}</small></div>`;
}

function exportReport() { window.location.href = `/api/reports/export.csv?${queryString({ month: state.month, company: state.company })}`; }

async function importWorkbook(event) {
  const file = event.target.files[0]; if (!file) return;
  try {
    const headers = { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "X-Filename": file.name };
    if (state.company !== "all") headers["X-Company-Id"] = state.company;
    const result = await api("/api/import/nfse", { method: "POST", headers, body: await file.arrayBuffer() });
    toast(`${result.inserted} registros importados; ${result.ignored} já existentes.`);
    state.metadata = await api("/api/meta"); populateDataLists(); await loadCurrentView();
  } catch (error) { toast(error.message, "error"); }
  finally { event.target.value = ""; }
}

function formatDateTime(value) {
  if (!value) return "Nunca entrou";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

async function loadUsers() {
  const users = await api("/api/users");
  state.users = users;
  $("#usersTable").innerHTML = users.length ? users.map(user => `
    <tr>
      <td class="user-name"><strong>${escapeHtml(user.name)}</strong><small>Cadastrado em ${formatDateTime(user.created_at)}</small></td>
      <td>${escapeHtml(user.username)}</td>
      <td><span class="badge ${user.role}">${user.role === "admin" ? "Administrador" : "Usuário comum"}</span></td>
      <td><span class="badge ${user.is_active ? "active" : "inactive"}">${user.is_active ? "Ativo" : "Inativo"}</span></td>
      <td>${formatDateTime(user.last_login_at)}</td>
      <td><div class="row-actions"><button data-edit-user="${user.id}">Editar</button></div></td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty-state">Nenhum usuário cadastrado.</td></tr>`;
  $$('[data-edit-user]').forEach(button => button.addEventListener("click", () => openUserDialog(Number(button.dataset.editUser))));
}

function openUserDialog(id = null) {
  const form = $("#userForm");
  form.reset();
  $("#userFormError").textContent = "";
  form.elements.id.value = id || "";
  form.elements.role.value = "user";
  form.elements.is_active.checked = true;
  form.elements.password.required = !id;
  $("#passwordHelp").textContent = id
    ? "Deixe em branco para manter a senha atual."
    : "Obrigatória, com no mínimo 8 caracteres.";
  $("#userDialogTitle").textContent = id ? "Editar usuário" : "Novo usuário";
  if (id) {
    const user = state.users.find(item => item.id === id);
    if (!user) return;
    form.elements.name.value = user.name;
    form.elements.username.value = user.username;
    form.elements.role.value = user.role;
    form.elements.is_active.checked = user.is_active;
  }
  $("#userDialog").showModal();
}

async function saveUser(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (event.submitter?.value === "cancel") { $("#userDialog").close(); return; }
  const payload = Object.fromEntries(new FormData(form));
  const id = payload.id;
  delete payload.id;
  payload.is_active = form.elements.is_active.checked;
  if (!payload.password) delete payload.password;
  try {
    await api(id ? `/api/users/${id}` : "/api/users", {
      method: id ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#userDialog").close();
    toast(id ? "Usuário atualizado." : "Usuário criado com sucesso.");
    await loadUsers();
  } catch (error) { $("#userFormError").textContent = error.message; }
}

function debounce(callback, delay) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => callback(...args).catch?.(error => toast(error.message, "error")), delay); };
}

initialize().catch(error => toast(error.message, "error"));
