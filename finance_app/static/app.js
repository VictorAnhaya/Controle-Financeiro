const state = {
  view: "dashboard",
  month: "",
  metadata: { categories: [], cost_centers: [] },
  transactions: [],
  documents: [],
  ranking: null,
  reviewDocument: null,
  dashboard: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const integer = new Intl.NumberFormat("pt-BR");
const statusLabel = { paid: "Pago/recebido", pending: "Pendente", overdue: "Vencido", cancelled: "Cancelado" };
const colors = ["#2f6fed", "#7657d6", "#d68a18", "#d94d5c", "#4f84c4", "#9a62b4", "#b56a43", "#52657e"];

function formatMoney(cents = 0) { return money.format(Number(cents) / 100); }
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
  wireEvents();
  state.metadata = await api("/api/meta");
  const currentMonth = new Date().toISOString().slice(0, 7);
  state.month = state.metadata.latest_month || currentMonth;
  $("#monthPicker").value = state.month;
  populateDataLists();
  await loadCurrentView();
}

function populateDataLists() {
  const categoryOptions = state.metadata.categories.map(item => `<option value="${escapeHtml(item)}"></option>`).join("");
  $("#allCategories").innerHTML = categoryOptions;
  $("#expenseCategories").innerHTML = categoryOptions;
  $("#categoryFilter").innerHTML = `<option value="">Todas</option>${state.metadata.categories.map(item => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("")}`;
  $("#costCenters").innerHTML = state.metadata.cost_centers.map(item => `<option value="${escapeHtml(item)}"></option>`).join("");
}

function wireEvents() {
  $$(".nav-item").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$('[data-go]').forEach(button => button.addEventListener("click", () => switchView(button.dataset.go)));
  $("#menuButton").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#monthPicker").addEventListener("change", async event => { state.month = event.target.value; await loadCurrentView(); });
  $("#newTransactionButton").addEventListener("click", () => openTransactionDialog());
  $("#transactionForm").addEventListener("submit", saveTransaction);
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
  $("#exportButton").addEventListener("click", exportReport);
  $("#clearFiltersButton").addEventListener("click", clearFilters);
  ["#kindFilter", "#statusFilter", "#categoryFilter"].forEach(selector => $(selector).addEventListener("change", loadTransactions));
  $("#searchFilter").addEventListener("input", debounce(loadTransactions, 300));
  window.addEventListener("resize", debounce(() => { if (state.view === "dashboard" && state.dashboard) renderCharts(state.dashboard); }, 180));
}

function switchView(view) {
  state.view = view;
  const titles = { dashboard: "Visão geral", transactions: "Lançamentos", documents: "Notas fiscais", ranking: "Ranking de clientes", budgets: "Orçamentos", reports: "Relatórios" };
  $("#pageTitle").textContent = titles[view];
  $$(".view").forEach(element => element.classList.remove("active"));
  $(`#${view}View`).classList.add("active");
  $$(".nav-item").forEach(element => element.classList.toggle("active", element.dataset.view === view));
  $("#sidebar").classList.remove("open");
  loadCurrentView().catch(error => toast(error.message, "error"));
}

async function loadCurrentView() {
  if (state.view === "dashboard") return loadDashboard();
  if (state.view === "transactions") return loadTransactions();
  if (state.view === "documents") return loadDocuments();
  if (state.view === "ranking") return loadRanking();
  if (state.view === "budgets") return loadBudgets();
  if (state.view === "reports") return loadReports();
}

async function loadDashboard() {
  const data = await api(`/api/dashboard?month=${state.month}`);
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
  return { month: state.month, kind: $("#kindFilter").value, status: $("#statusFilter").value, category: $("#categoryFilter").value, search: $("#searchFilter").value.trim() };
}

async function loadTransactions() {
  const rows = await api(`/api/transactions?${queryString(transactionFilters())}`);
  state.transactions = rows;
  $("#transactionsTable").innerHTML = rows.length ? rows.map(row => `
    <tr>
      <td>${formatDate(row.transaction_date)}</td>
      <td class="transaction-name"><strong>${escapeHtml(row.description)}</strong><small>${escapeHtml(row.counterparty || row.cost_center || "Sem favorecido")}</small></td>
      <td>${escapeHtml(row.category)}</td>
      <td><span class="badge ${row.status}">${statusLabel[row.status]}</span></td>
      <td class="align-right"><strong class="amount-${row.kind}">${row.kind === "expense" ? "−" : "+"} ${formatMoney(row.amount_cents)}</strong></td>
      <td><div class="row-actions"><button data-edit="${row.id}">Editar</button><button class="delete" data-delete="${row.id}">Excluir</button></div></td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty-state">Nenhum lançamento encontrado.</td></tr>`;
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
  $("#dialogTitle").textContent = id ? "Editar lançamento" : "Novo lançamento";
  if (id) {
    const row = state.transactions.find(item => item.id === id);
    if (!row) return;
    Object.entries(row).forEach(([key, value]) => { if (form.elements[key] && value !== null) form.elements[key].value = value; });
    form.elements.amount.value = (row.amount_cents / 100).toFixed(2).replace(".", ",");
  }
  $("#transactionDialog").showModal();
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

async function loadBudgets() {
  const rows = await api(`/api/budgets?month=${state.month}`);
  $("#budgetCards").innerHTML = rows.length ? rows.map(row => {
    const usage = row.limit_cents ? Math.round(row.used_cents / row.limit_cents * 100) : 0;
    const level = usage > 100 ? "danger" : usage >= 80 ? "warning" : "";
    return `<article class="budget-card"><header><h3>${escapeHtml(row.category)}</h3><span>${usage}% utilizado</span></header><div class="progress"><i class="${level}" style="width:${Math.min(usage, 100)}%"></i></div><footer><span>Gasto <strong>${formatMoney(row.used_cents)}</strong></span><span>Limite <strong>${formatMoney(row.limit_cents)}</strong></span></footer></article>`;
  }).join("") : `<article class="panel empty-state">Nenhum orçamento definido para este período.</article>`;
}

async function loadDocuments() {
  const documents = await api("/api/documents");
  state.documents = documents;
  $("#documentCount").textContent = `${integer.format(documents.length)} documento${documents.length === 1 ? "" : "s"}`;
  const documentStatus = { analyzed: "Analisado", needs_review: "Revisar", linked: "Vinculado" };
  $("#documentsTable").innerHTML = documents.length ? documents.map(document => `
    <tr>
      <td class="document-file"><strong>${escapeHtml(document.file_name)}</strong><small>${escapeHtml(document.document_type)}${document.document_number ? ` · Nº ${escapeHtml(document.document_number)}` : ""}</small></td>
      <td>${escapeHtml(document.issuer_name || "Não identificado")}</td>
      <td>${formatDate(document.issue_date)}</td>
      <td><span class="badge ${document.extraction_status}">${documentStatus[document.extraction_status]}</span></td>
      <td class="align-right"><strong>${document.total_cents ? formatMoney(document.total_cents) : "—"}</strong></td>
      <td><div class="document-actions"><a href="/api/documents/${document.id}/file" target="_blank" rel="noopener">Abrir</a>${document.transaction_id ? "" : `<button data-review-document="${document.id}">Conferir</button>`}</div></td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty-state">Nenhum documento anexado.</td></tr>`;
  $$('[data-review-document]').forEach(button => button.addEventListener("click", () => {
    const document = state.documents.find(item => item.id === Number(button.dataset.reviewDocument));
    if (document) openDocumentReview(document);
  }));
}

async function analyzeDocument(file) {
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) { toast("O arquivo excede o limite de 15 MB.", "error"); return; }
  toast("Lendo e conferindo a nota fiscal...");
  try {
    const document = await api("/api/documents/analyze", {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
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
  const data = await api(`/api/ranking?${queryString({ month: state.month, scope })}`);
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
  const payload = Object.fromEntries(new FormData(event.currentTarget)); payload.month = state.month;
  try { await api("/api/budgets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); event.currentTarget.reset(); toast("Orçamento salvo."); await loadBudgets(); }
  catch (error) { toast(error.message, "error"); }
}

async function loadReports() {
  const data = await api(`/api/dashboard?month=${state.month}`);
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

function exportReport() { window.location.href = `/api/reports/export.csv?${queryString({ month: state.month })}`; }

async function importWorkbook(event) {
  const file = event.target.files[0]; if (!file) return;
  try {
    const result = await api("/api/import/nfse", { method: "POST", headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "X-Filename": file.name }, body: await file.arrayBuffer() });
    toast(`${result.inserted} registros importados; ${result.ignored} já existentes.`);
    state.metadata = await api("/api/meta"); populateDataLists(); await loadCurrentView();
  } catch (error) { toast(error.message, "error"); }
  finally { event.target.value = ""; }
}

function debounce(callback, delay) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => callback(...args).catch?.(error => toast(error.message, "error")), delay); };
}

initialize().catch(error => toast(error.message, "error"));
