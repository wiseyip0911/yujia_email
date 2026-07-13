const state = {
  tasks: [],
  contexts: [],
  manuscripts: [],
  mailboxes: [],
  microsoftOAuth: null,
  daily: null,
  auditLogs: [],
  revisionJobs: [],
  syncIssues: [],
  taskFilter: "pending",
  focusContextId: null,
  searchQuery: "",
  searchResults: null,
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error(payload.error || "请先登录");
  }
  if (!response.ok) {
    throw new Error(payload.error || "请求失败");
  }
  return payload;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatConfidence(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

const WORKFLOW_STATUS_LABELS = {
  Accepted: "已接收",
  Rejected: "已拒稿",
  "Revision Requested": "要求返修",
  Proof: "校样",
  "APC Payment": "版面费付款",
  "Under Review": "审稿中",
  "New Submission": "新投稿",
  "Non-submission": "非投稿邮件",
  "Follow Up": "跟进事项",
  New: "新建",
};

const CATEGORY_LABELS = {
  accepted: "接收",
  rejected: "拒稿",
  revision: "返修",
  proof: "校样",
  payment: "版面费",
  under_review: "审稿中",
  submission: "投稿",
  other: "非投稿",
  unclassified: "未分类",
};

const INTERNAL_VALUE_LABELS = {
  "Unmatched Manuscript": "未匹配稿件",
  "Unknown Journal": "未知期刊",
};

function humanText(value, fallback = "未识别") {
  if (value === null || value === undefined || value === "") return fallback;
  let text = String(value);
  const replacements = { ...WORKFLOW_STATUS_LABELS, ...INTERNAL_VALUE_LABELS };
  Object.entries(replacements)
    .sort((a, b) => b[0].length - a[0].length)
    .forEach(([from, to]) => {
      text = text.replaceAll(from, to);
    });
  return text || fallback;
}

function workflowStatusText(value, fallback = "未识别") {
  if (value === null || value === undefined || value === "") return fallback;
  return WORKFLOW_STATUS_LABELS[value] || humanText(value, fallback);
}

function categoryText(value) {
  if (value === null || value === undefined || value === "") return "未分类";
  return CATEGORY_LABELS[value] || humanText(value, "未分类");
}

function journalText(value) {
  return humanText(value, "未知期刊");
}

function displayTitle(value, fallback = "未匹配稿件") {
  const text = humanText(value, fallback);
  if (text.startsWith("Manuscript ")) {
    return `稿件 ${text.replace("Manuscript ", "")}`;
  }
  return text;
}

async function loadAll() {
  const [dashboard, tasks, contexts, manuscripts, mailboxes, microsoftOAuth, daily, audit, revisionJobs, syncIssues] = await Promise.all([
    api("/api/dashboard"),
    api("/api/review-tasks"),
    api("/api/contexts"),
    api("/api/manuscripts"),
    api("/api/mailboxes"),
    api("/api/oauth/microsoft/status"),
    api("/api/reports/daily"),
    api("/api/audit-logs"),
    api("/api/revision-jobs"),
    api("/api/sync-issues"),
  ]);
  state.tasks = tasks.items;
  state.contexts = contexts.items;
  state.manuscripts = manuscripts.items;
  state.mailboxes = mailboxes.items;
  state.microsoftOAuth = microsoftOAuth;
  state.daily = daily;
  state.auditLogs = audit.items;
  state.revisionJobs = revisionJobs.items;
  state.syncIssues = syncIssues.items;
  renderDashboard(dashboard);
  renderCommandCenter();
  renderSearchResults();
  renderTasks();
  renderContexts();
  renderManuscripts();
  renderMailboxes();
  renderRevisionJobs();
  renderOpenReminders();
  renderSyncIssues();
  renderDaily();
  renderAudit();
}

function renderDashboard(data) {
  $("#pendingReview").textContent = `${data.cards.pending_review} 待审核`;
  $("#contextCount").textContent = businessContexts().length;
  $("#manuscriptCount").textContent = data.cards.manuscripts;
  $("#openReminderCount").textContent = data.cards.open_reminders;
  $("#failedSyncCount").textContent = data.cards.failed_syncs;
  $("#mailboxTotal").textContent = state.mailboxes.length;
  $("#revisionJobCount").textContent = state.revisionJobs.length;
}

function renderCommandCenter() {
  const pending = pendingTasks();
  const recommendedHandle = pending.filter((task) => ["handle", "judge"].includes(taskDecision(task).type));
  const revisionPending = pending.filter((task) => taskDecision(task).type === "revision_manual");
  const recommendedSkip = pending.filter((task) => taskDecision(task).type === "skip");
  const mailboxIssues = issueMailboxes();
  const activeMailboxes = state.mailboxes.filter((item) => item.status === "active").length;
  const openReminders = actionableReminders().length;
  const focusCount = revisionPending.length || recommendedHandle.length || recommendedSkip.length || openReminders;

  $("#todoTotal").textContent = focusCount;
  $("#handleCount").textContent = recommendedHandle.length;
  $("#skipCount").textContent = recommendedSkip.length;
  $("#mailboxIssueCount").textContent = mailboxIssues.length;
  $("#todoCaption").textContent = pending.length ? `${pending.length} 封邮件待判断` : `${openReminders} 个提醒待跟进`;
  $("#todoSummary").textContent =
    pending.length > 0
      ? `先标记 ${revisionPending.length} 项返修为需手动处理，再处理 ${recommendedHandle.length} 项业务邮件，最后清理 ${recommendedSkip.length} 项非投稿邮件`
      : "邮件判断队列已清空";
  $("#headerStatus").textContent = `${pending.length} 封邮件待判断，${businessContexts().length} 条业务脉络，${activeMailboxes} 个邮箱可采集，${mailboxIssues.length} 个接入异常`;

  renderTodoList(pending, mailboxIssues);
  renderOpsSnapshot(activeMailboxes, mailboxIssues);
}

function pendingTasks() {
  return state.tasks.filter((task) => task.status === "pending");
}

function issueMailboxes() {
  return state.mailboxes
    .filter((item) => item.status !== "active")
    .sort((a, b) => String(b.last_tested_at || "").localeCompare(String(a.last_tested_at || "")));
}

function actionableReminders() {
  return (state.daily?.open_reminders || []).filter((reminder) => {
    const type = String(reminder.reminder_type || "").toLowerCase();
    const title = String(reminder.title || "").toLowerCase();
    return !["non-submission", "other"].includes(type) && !title.includes("unmatched manuscript");
  });
}

function taskDecision(task) {
  const fields = task.extracted || {};
  const nextStatus = String(fields.next_status || "").toLowerCase();
  const category = String(task.category || fields.category || "").toLowerCase();
  const confidence = Number(task.confidence || 0);
  if (task.status !== "pending") {
    const isRevision = task.status === "needs_revision";
    const isRevisionManual = task.status === "revision_manual_required" || task.status === "revision_handoff";
    return {
      type: isRevision ? "revision" : "done",
      label: isRevision ? "待调整" : isRevisionManual ? "需手动处理" : "已处理",
      badge: isRevision || isRevisionManual ? "warn" : "status",
      action: isRevision ? "待调整" : isRevisionManual ? "需手动处理" : "已确认",
    };
  }
  if (category === "revision" || nextStatus === "revision requested") {
    return {
      type: "revision_manual",
      label: "返修需手动处理",
      badge: "warn",
      action: "标记需手动处理",
    };
  }
  if (category === "other" || nextStatus === "non-submission") {
    return {
      type: "skip",
      label: "建议不处理",
      badge: "quiet",
      action: "确认无需处理",
    };
  }
  if (confidence < 0.75) {
    return {
      type: "judge",
      label: "需要判断",
      badge: "warn",
      action: "确认处理结果",
    };
  }
  return {
    type: "handle",
    label: "建议处理",
    badge: "attention",
    action: "确认并更新状态",
  };
}

function todoPriority(decision) {
  if (decision.type === "revision_manual") return 0;
  if (decision.type === "handle") return 0;
  if (decision.type === "judge") return 1;
  if (decision.type === "skip") return 2;
  return 3;
}

function sortedTasks(tasks) {
  return [...tasks].sort((a, b) => {
    const priorityDiff = todoPriority(taskDecision(a)) - todoPriority(taskDecision(b));
    if (priorityDiff) return priorityDiff;
    return String(b.received_at || "").localeCompare(String(a.received_at || ""));
  });
}

function contextVisibleInMain(context) {
  return !["account_notice", "mailbox_notice"].includes(context.context_type);
}

function businessContexts() {
  return state.contexts.filter(contextVisibleInMain);
}

function contextForTask(task) {
  return businessContexts().find((context) => (context.email_ids || []).includes(task.email_id));
}

function taskById(taskId) {
  return state.tasks.find((task) => String(task.task_id) === String(taskId));
}

function contextById(contextId) {
  return state.contexts.find((context) => String(context.context_id) === String(contextId));
}

function isNoActionTask(task) {
  const fields = task.extracted || {};
  const nextStatus = String(fields.next_status || "").toLowerCase();
  const category = String(task.category || fields.category || "").toLowerCase();
  return category === "other" || nextStatus === "non-submission";
}

function isRevisionTask(task) {
  const fields = task.extracted || {};
  const nextStatus = String(fields.next_status || "").toLowerCase();
  const category = String(task.category || fields.category || "").toLowerCase();
  return category === "revision" || nextStatus === "revision requested";
}

function taskTargetLabel(task) {
  const fields = task.extracted || {};
  const context = contextForTask(task);
  return (
    fields.manuscript_no ||
    context?.manuscript_no ||
    context?.project_code ||
    displayTitle(fields.title || context?.title, "未匹配稿件")
  );
}

function taskProcessingOptions(task) {
  const fields = task.extracted || {};
  const target = taskTargetLabel(task);
  const nextStatus = workflowStatusText(fields.next_status, "当前建议状态");
  const options = [];
  if (isRevisionTask(task)) {
    options.push({
      id: "manual_required",
      title: "标记需手动处理",
      recommended: true,
      badge: "warn",
      summary: `功能尚未完成，不自动处理返修；只把 ${target} 标记为需手动处理。`,
      items: ["任务状态改为需手动处理", "不写入普通稿件事件流", "返修页继续保留这封邮件", "审计日志记录需手动处理"],
      actionLabel: "按此方式处理",
    });
  } else if (isNoActionTask(task)) {
    options.push({
      id: "no_action",
      title: "无需处理，归档此邮件",
      recommended: true,
      badge: "quiet",
      summary: "适用于系统通知、非投稿邮件等，不进入稿件流程。",
      items: ["任务状态改为已处理", "不新建稿件档案", "不写入稿件事件流", "审计日志记录无需处理"],
      actionLabel: "按此方式处理",
    });
  } else {
    options.push({
      id: "confirm_status",
      title: `更新稿件状态为 ${nextStatus}`,
      recommended: taskDecision(task).type !== "judge",
      badge: "attention",
      summary: `把当前邮件作为正式业务事件，写入 ${target}。`,
      items: [
        `稿件状态更新为：${nextStatus}`,
        `作用对象：${target}`,
        ...(fields.due_date ? [`生成或保留截止提醒：${fields.due_date}`] : []),
        "任务从待判断队列移除",
        "稿件事件流和审计日志会新增记录",
      ],
      actionLabel: "按此方式处理",
    });
  }
  options.push({
    id: "needs_revision",
    title: "先不处理，改为待调整",
    recommended: taskDecision(task).type === "judge",
    badge: "warn",
    summary: "适用于识别不确定、信息不足、需要人工重新判断的邮件。",
    items: ["不更新稿件状态", "不写入稿件事件流", "任务状态改为待调整", "审计日志记录需人工调整"],
    actionLabel: "按此方式处理",
  });
  return options;
}

function recommendedProcessingOption(task) {
  return taskProcessingOptions(task).find((option) => option.recommended) || taskProcessingOptions(task)[0];
}

function taskProcessingPreviewHtml(task) {
  const recommended = recommendedProcessingOption(task);
  return `
    <div class="process-preview">
      <div>
        <span>系统推荐</span>
        <strong>${escapeHtml(recommended.title)}</strong>
        <p>${escapeHtml(recommended.summary)}</p>
      </div>
    </div>
  `;
}

function renderTodoList(pending, mailboxIssues) {
  const orderedTasks = sortedTasks(pending);
  const focusTask = orderedTasks[0];
  const handleCount = pending.filter((task) => ["handle", "judge"].includes(taskDecision(task).type)).length;
  const skipCount = pending.filter((task) => taskDecision(task).type === "skip").length;
  const reminders = actionableReminders();
  const reminderCount = reminders.length;
  const revisionCount = state.revisionJobs.length;
  const latestMailboxIssue = mailboxIssues[0];
  let focusHtml = "";

  if (focusTask) {
    const decision = taskDecision(focusTask);
    const fields = focusTask.extracted || {};
    const context = contextForTask(focusTask);
    const focusCopy = taskDisplayCopy(focusTask);
    focusHtml = `
      <article class="focus-task ${escapeHtml(decision.type)}">
        <div class="focus-copy">
          <span class="badge ${decision.badge}">${escapeHtml(decision.label)}</span>
          <h3>${escapeHtml(focusCopy.subject)}</h3>
          <p>${escapeHtml(focusCopy.summary)}${context ? ` · 已关联 ${escapeHtml(context.email_count)} 封同脉络邮件` : ""}</p>
          <div class="focus-facts">
            ${compactFact("发件人", focusTask.sender)}
            ${compactFact("类别", `${categoryText(focusTask.category)} · ${formatConfidence(focusTask.confidence)}`)}
            ${compactFact("稿件编号", fields.manuscript_no || "未识别")}
            ${compactFact("截止日期", fields.due_date || "无")}
            ${compactFact("业务脉络", context ? displayTitle(context.title, "未形成") : "未形成")}
          </div>
          ${taskProcessingPreviewHtml(focusTask)}
        </div>
        <div class="focus-action">
          <button class="primary" data-process-task="${focusTask.task_id}" type="button">处理</button>
          ${context ? `<button data-open-context="${escapeHtml(context.context_id)}" type="button">查看脉络</button>` : ""}
          <button data-task-filter="${decision.type === "skip" ? "skip" : "handle"}" type="button">查看同类邮件</button>
        </div>
      </article>
    `;
  } else if (reminderCount) {
    const reminder = reminders[0];
    focusHtml = `
      <article class="focus-task reminder">
        <div class="focus-copy">
          <span class="badge warn">开放提醒</span>
          <h3>${escapeHtml(displayTitle(reminder.title || workflowStatusText(reminder.reminder_type, "跟进提醒")))}</h3>
          <p>${escapeHtml(workflowStatusText(reminder.reminder_type, "跟进提醒"))}${reminder.due_date ? ` · ${escapeHtml(reminder.due_date)}` : ""}</p>
          <div class="focus-facts">
            ${compactFact("稿件编号", reminder.manuscript_no || "无编号")}
            ${compactFact("负责人", reminder.owner || "未分配")}
          </div>
        </div>
        <div class="focus-action">
          <button class="primary" data-open-view="daily" type="button">查看日报</button>
        </div>
      </article>
    `;
  } else {
    focusHtml = `
      <article class="focus-task empty-focus">
        <div class="focus-copy">
          <span class="badge ok">队列已清空</span>
          <h3>当前没有邮件待判断</h3>
          <p>下一步可以采集新邮件，或查看邮箱接入状态是否影响后续同步。</p>
          <div class="focus-facts">
            ${compactFact("可采集邮箱", state.mailboxes.filter((item) => item.status === "active").length)}
            ${compactFact("接入异常", mailboxIssues.length)}
          </div>
        </div>
        <div class="focus-action">
          <button class="primary" data-fetch-emails type="button">采集/识别邮件</button>
          <button data-open-view="mailboxes" type="button">查看邮箱状态</button>
        </div>
      </article>
    `;
  }

  const queueHtml = `
    <div class="queue-strip" aria-label="处理队列">
      ${queueButton("handle", "业务邮件", handleCount, "建议处理和低置信度判断")}
      ${queueButton("skip", "非投稿邮件", skipCount, "可确认无需处理")}
      ${queueLink("revision", "返修处理", revisionCount, "需手动处理")}
      ${queueLink("contexts", "业务脉络", businessContexts().length, "按项目/作者/稿件串联")}
      ${queueLink("daily", "开放提醒", reminderCount, "返修/版面费/校样等")}
      ${queueLink("mailboxes", "接入异常", mailboxIssues.length, latestMailboxIssue ? latestMailboxIssue.masked_email : "暂无异常")}
    </div>
  `;

  $("#todoList").innerHTML = focusHtml + queueHtml;
}

function compactFact(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "无")}</strong></div>`;
}

function queueButton(filterName, label, count, detail) {
  return `
    <button class="queue-card" data-task-filter="${escapeHtml(filterName)}" type="button">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(count)}</strong>
      <small>${escapeHtml(detail)}</small>
    </button>
  `;
}

function queueLink(viewName, label, count, detail) {
  return `
    <button class="queue-card" data-open-view="${escapeHtml(viewName)}" type="button">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(count)}</strong>
      <small>${escapeHtml(detail)}</small>
    </button>
  `;
}

function renderOpsSnapshot(activeMailboxes, mailboxIssues) {
  const oauth = state.microsoftOAuth || {};
  const daily = state.daily || { events: [], open_reminders: [] };
  $("#opsSnapshot").innerHTML = `
    <div class="ops-row">
      <span>邮箱采集</span>
      <strong>${activeMailboxes}/${state.mailboxes.length}</strong>
      <button data-open-view="mailboxes" type="button">邮箱状态</button>
    </div>
    <div class="ops-row">
      <span>Microsoft 授权</span>
      <strong>${escapeHtml(oauth.linked_mailboxes || 0)}/${escapeHtml(oauth.total_microsoft_mailboxes || 0)}</strong>
      <button data-open-view="mailboxes" type="button">处理授权</button>
    </div>
    <div class="ops-row">
      <span>今日正式事件</span>
      <strong>${daily.events.length}</strong>
      <button data-open-view="daily" type="button">看日报</button>
    </div>
    <div class="ops-row">
      <span>接入异常</span>
      <strong>${mailboxIssues.length}</strong>
      <button data-open-view="mailboxes" type="button">排查</button>
    </div>
    <div class="ops-actions">
      <button data-fetch-emails class="primary" type="button">采集/识别</button>
      <button data-export-kingdee type="button">金蝶导出</button>
    </div>
  `;
}

function renderTasks() {
  const container = $("#reviewTasks");
  const visibleTasks = filteredTasks();
  $("#taskCount").textContent = `${visibleTasks.length} 项`;
  if (!visibleTasks.length) {
    container.innerHTML = `<div class="empty">暂无审核任务。点击“采集/识别邮件”生成任务。</div>`;
    return;
  }
  container.innerHTML = visibleTasks
    .map((task) => {
      const fields = task.extracted || {};
      const isPending = task.status === "pending";
      const decision = taskDecision(task);
      const context = contextForTask(task);
      const copy = taskDisplayCopy(task);
      return `
        <article class="task ${escapeHtml(task.status)}">
          <div class="task-title">
            <h3>${escapeHtml(copy.subject)}</h3>
            <span class="badge ${decision.badge}">${escapeHtml(decision.label)}</span>
          </div>
          <div class="meta-row">
            <span>${escapeHtml(task.sender)}</span>
            <span>${escapeHtml(task.received_at)}</span>
            <span class="badge ${task.confidence < 0.75 ? "warn" : "status"}">${escapeHtml(categoryText(task.category))} · ${formatConfidence(task.confidence)}</span>
          </div>
          ${taskEvidenceHtml(task)}
          <div class="fields">
            ${field("稿件编号", fields.manuscript_no)}
            ${field("期刊", journalText(fields.journal))}
            ${field("状态建议", workflowStatusText(fields.next_status))}
            ${field("截止日期", fields.due_date)}
            ${field("关联脉络", context ? `${displayTitle(context.title)}（${context.email_count} 封）` : "未形成")}
          </div>
          ${isPending ? taskProcessingPreviewHtml(task) : ""}
          <div class="task-actions">
            ${context ? `<button data-open-context="${escapeHtml(context.context_id)}" type="button">看上下文</button>` : ""}
            ${
              isPending
                ? `
                  <button class="primary" data-process-task="${task.task_id}" type="button">处理</button>
                `
                : `<span class="badge ${decision.badge}">${escapeHtml(decision.label)}</span>`
            }
          </div>
        </article>
      `;
    })
    .join("");
}

function filteredTasks() {
  const tasks = state.taskFilter === "all" ? state.tasks : state.tasks.filter((task) => task.status === "pending");
  if (state.taskFilter === "handle") {
    return sortedTasks(tasks).filter((task) => ["handle", "judge"].includes(taskDecision(task).type));
  }
  if (state.taskFilter === "skip") {
    return sortedTasks(tasks).filter((task) => taskDecision(task).type === "skip");
  }
  return sortedTasks(tasks);
}

function taskDisplayCopy(task) {
  const fields = task.extracted || {};
  const subject = task.is_english_subject ? task.subject_translated || task.subject : task.subject;
  const summary = task.is_english_subject
    ? humanText(task.snippet_translated || fields.next_action || fields.next_status || task.evidence, "等待人工确认")
    : humanText(fields.next_action || fields.next_status || task.evidence, "等待人工确认");
  return { subject, summary };
}

function taskEvidenceHtml(task) {
  if (task.is_english_subject) {
    return `
      <div class="evidence translated-evidence">
        <div>
          <span>中文译文</span>
          <p>${escapeHtml(humanText(task.snippet_translated, "暂未生成译文，请查看英文原文。"))}</p>
        </div>
        <div>
          <span>英文原文</span>
          <p><strong>主题：</strong>${escapeHtml(task.subject_original || task.subject || "无主题")}</p>
          <p>${escapeHtml(task.snippet_original || task.body_text || task.evidence || "暂无原文摘录")}</p>
        </div>
        ${task.evidence ? `<div><span>识别依据</span><p>${escapeHtml(humanText(task.evidence))}</p></div>` : ""}
      </div>
    `;
  }
  return `<div class="evidence">证据：${escapeHtml(humanText(task.evidence))}</div>`;
}

function field(label, value) {
  return `<div class="field"><span>${escapeHtml(label)}</span>${escapeHtml(value || "未识别")}</div>`;
}

function renderContexts() {
  const container = $("#contexts");
  const contexts = businessContexts();
  if (!contexts.length) {
    container.innerHTML = `<div class="empty">暂无业务邮件上下文。系统通知和账号通知会自动归档，不进入这里。</div>`;
    return;
  }
  container.innerHTML = contexts
    .map((context) => {
      const timeline = context.timeline || [];
      const isFocused = state.focusContextId === context.context_id;
      return `
        <article class="record context-record ${isFocused ? "focused" : ""}" data-context-card="${escapeHtml(context.context_id)}">
          <div class="context-head">
            <div>
              <span class="badge status">${escapeHtml(contextTypeLabel(context.context_type))}</span>
              <h3>${escapeHtml(displayTitle(context.title))}</h3>
            </div>
            <div class="context-counts">
              <span class="badge status">共 ${escapeHtml(context.email_count)} 封</span>
              <span class="badge ${context.pending_count ? "warn" : "ok"}">${escapeHtml(context.pending_count)} 待判断</span>
            </div>
          </div>
          <div class="context-overview">
            ${contextFact("客户", context.customer_name || "未知客户")}
            ${contextFact("作者", context.author_name || "未知作者")}
            ${contextFact("项目/稿件", context.project_code || context.manuscript_no || "无项目编号")}
            ${contextFact("最近邮件", context.latest_received_at || "无")}
          </div>
          <div class="context-actionline">
            <div>
              <span>当前判断</span>
              <strong>${escapeHtml(workflowStatusText(context.current_status))}</strong>
            </div>
            <div>
              <span>建议动作</span>
              <strong>${escapeHtml(humanText(context.suggested_action, "等待判断"))}</strong>
            </div>
          </div>
          <div class="context-timeline">
            <div class="context-timeline-head">
              <strong>邮件列表</strong>
              <span>共 ${escapeHtml(context.email_count)} 封，当前显示最近 ${escapeHtml(timeline.length)} 封</span>
            </div>
            ${contextEmailRows(context)}
          </div>
        </article>
      `;
    })
    .join("");
}

function contextFact(label, value) {
  return `
    <div class="context-fact">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "无")}</strong>
    </div>
  `;
}

function contextEmailRows(context) {
  const timeline = context.timeline || [];
  if (!timeline.length) {
    return `<div class="empty">这条脉络还没有可展示的邮件。</div>`;
  }
  return timeline
    .map((item, index) => {
      const evidence =
        !["account_notice", "mailbox_notice"].includes(context.context_type) && item.evidence && item.evidence !== item.snippet
          ? item.evidence
          : "";
      const statusText = contextEmailStatus(context, item);
      const subjectLabel = item.is_english_subject ? "中文主题" : "主题";
      const displaySubject = item.is_english_subject ? humanText(item.subject_translated || item.subject, "无主题") : item.subject;
      return `
        <div class="context-email-row">
          <div class="context-email-index">
            <strong>邮件 ${index + 1}</strong>
            <span>/ 共 ${escapeHtml(context.email_count)}</span>
          </div>
          <div class="context-email-body">
            <div class="context-email-title">
              <div>
                <span>${escapeHtml(subjectLabel)}</span>
                <strong>${escapeHtml(displaySubject || "无主题")}</strong>
              </div>
              <span class="badge ${item.task_status === "pending" ? "warn" : "status"}">${escapeHtml(statusText)}</span>
            </div>
            <div class="context-email-meta">
              <span>发件人：${escapeHtml(item.sender || "未知")}</span>
              <span>时间：${escapeHtml(item.received_at || "未知")}</span>
            </div>
            <div class="context-email-fields">
              ${contextEmailContentFields(item)}
              ${
                evidence
                  ? `<div><span>识别依据</span><p>${escapeHtml(humanText(evidence))}</p></div>`
                  : ""
              }
            </div>
          </div>
        </div>
      `;
    })
    .join("");
}

function contextEmailContentFields(item) {
  const originalSubject = item.subject_original || item.subject || "无主题";
  const originalSnippet = item.snippet_original || item.snippet || item.evidence || item.next_action || "暂无原文摘录";
  if (item.is_english_subject) {
    return `
      <div class="translation-field">
        <span>中文译文</span>
        <p>${escapeHtml(humanText(item.snippet_translated, "暂未生成译文，请查看英文原文。"))}</p>
      </div>
      <div class="original-field">
        <span>英文原文</span>
        <p><strong>主题：</strong>${escapeHtml(originalSubject)}</p>
        <p>${escapeHtml(originalSnippet)}</p>
      </div>
    `;
  }
  return `
    <div>
      <span>邮件内容摘录</span>
      <p>${escapeHtml(originalSnippet)}</p>
    </div>
  `;
}

function contextEmailStatus(context, item) {
  if (context.context_type === "account_notice") return "账号通知";
  if (context.context_type === "mailbox_notice") return "系统通知";
  return item.next_status ? workflowStatusText(item.next_status) : categoryText(item.category);
}

function contextTypeLabel(type) {
  const labels = {
    manuscript: "稿件",
    project: "项目",
    account_notice: "账号通知",
    mailbox_notice: "邮箱通知",
    author: "作者",
    thread: "会话",
    mailbox: "邮箱",
  };
  return labels[type] || "上下文";
}

function emailContextForId(emailId) {
  return businessContexts().find((context) => (context.email_ids || []).includes(emailId));
}

function openContext(contextId) {
  openContextModal(contextId);
}

function openModal({ kicker, title, bodyHtml, actionsHtml }) {
  $("#modalKicker").textContent = kicker || "";
  $("#modalTitle").textContent = title || "详情";
  $("#modalBody").innerHTML = bodyHtml || "";
  $("#modalActions").innerHTML = actionsHtml || `<button data-close-modal type="button">关闭</button>`;
  $("#modalBackdrop").classList.remove("hidden");
  document.body.classList.add("modal-open");
}

function closeModal() {
  $("#modalBackdrop").classList.add("hidden");
  document.body.classList.remove("modal-open");
  $("#modalBody").innerHTML = "";
  $("#modalActions").innerHTML = "";
}

function effectListHtml(items) {
  return `<ul class="effect-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function taskModalEvidenceHtml(task) {
  const fields = task.extracted || {};
  return `
    <div class="modal-task-summary">
      ${contextFact("主题", taskDisplayCopy(task).subject)}
      ${contextFact("发件人", task.sender || "未知")}
      ${contextFact("系统分类", `${categoryText(task.category || fields.category)} · ${formatConfidence(task.confidence)}`)}
      ${contextFact("状态建议", workflowStatusText(fields.next_status, "无需处理"))}
      ${contextFact("稿件编号", fields.manuscript_no || "未识别")}
      ${contextFact("截止日期", fields.due_date || "无")}
    </div>
    ${taskEvidenceHtml(task)}
  `;
}

function processingOptionButtonHtml(task, option) {
  if (option.id === "manual_required") {
    return `<button class="primary" data-execute-revision-manual="${escapeHtml(task.task_id)}" type="button">${escapeHtml(option.actionLabel)}</button>`;
  }
  if (option.id === "needs_revision") {
    return `<button class="primary" data-execute-revise="${escapeHtml(task.task_id)}" type="button">${escapeHtml(option.actionLabel)}</button>`;
  }
  return `<button class="primary" data-execute-confirm="${escapeHtml(task.task_id)}" type="button">${escapeHtml(option.actionLabel)}</button>`;
}

function processingOptionHtml(task, option) {
  return `
    <article class="process-option ${option.recommended ? "recommended" : ""}">
      <div class="process-option-head">
        <div>
          <span class="badge ${option.badge}">${escapeHtml(option.recommended ? "系统推荐" : "可选处理")}</span>
          <h3>${escapeHtml(option.title)}</h3>
        </div>
      </div>
      <p>${escapeHtml(option.summary)}</p>
      <strong class="process-result-label">执行后结果</strong>
      ${effectListHtml(option.items)}
      <div class="process-option-action">
        ${processingOptionButtonHtml(task, option)}
      </div>
    </article>
  `;
}

function openProcessModal(taskId) {
  const task = taskById(taskId);
  if (!task) {
    showToast("找不到这条判断任务");
    return;
  }
  const options = taskProcessingOptions(task);
  openModal({
    kicker: "选择处理方式",
    title: "处理这封邮件",
    bodyHtml: `
      ${taskModalEvidenceHtml(task)}
      <section class="process-options">
        ${options.map((option) => processingOptionHtml(task, option)).join("")}
      </section>
    `,
    actionsHtml: `
      <button data-close-modal type="button">关闭</button>
    `,
  });
}

function openContextModal(contextId) {
  const context = contextById(contextId);
  if (!context) {
    showToast("找不到这条邮件脉络");
    return;
  }
  state.focusContextId = contextId;
  renderContexts();
  openModal({
    kicker: contextTypeLabel(context.context_type),
    title: displayTitle(context.title),
    bodyHtml: `
      <div class="modal-context-summary">
        ${contextFact("客户", context.customer_name || "未知客户")}
        ${contextFact("作者", context.author_name || "未知作者")}
        ${contextFact("项目/稿件", context.project_code || context.manuscript_no || "无项目编号")}
        ${contextFact("邮件数量", `${context.email_count} 封`)}
        ${contextFact("待判断", `${context.pending_count} 项`)}
        ${contextFact("最近邮件", context.latest_received_at || "无")}
      </div>
      <div class="context-actionline">
        <div>
          <span>当前判断</span>
          <strong>${escapeHtml(workflowStatusText(context.current_status))}</strong>
        </div>
        <div>
          <span>建议动作</span>
          <strong>${escapeHtml(humanText(context.suggested_action, "等待判断"))}</strong>
        </div>
      </div>
      <div class="context-timeline modal-timeline">
        <div class="context-timeline-head">
          <strong>邮件列表</strong>
          <span>共 ${escapeHtml(context.email_count)} 封，当前显示最近 ${escapeHtml((context.timeline || []).length)} 封</span>
        </div>
        ${contextEmailRows(context)}
      </div>
    `,
    actionsHtml: `
      <button data-close-modal type="button">关闭</button>
      <button class="primary" data-open-view-from-modal="contexts" type="button">打开上下文页</button>
    `,
  });
}

async function handleSearch(event) {
  event?.preventDefault();
  const query = $("#globalSearchInput").value.trim();
  state.searchQuery = query;
  if (!query) {
    state.searchResults = null;
    renderSearchResults();
    return;
  }
  state.searchResults = await api(`/api/search?q=${encodeURIComponent(query)}`);
  renderSearchResults();
}

function renderSearchResults() {
  const panel = $("#searchResultsPanel");
  if (!panel) return;
  const results = state.searchResults;
  if (!state.searchQuery || !results) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    return;
  }
  const total =
    results.contexts.filter(contextVisibleInMain).length +
    results.emails.length +
    results.manuscripts.length +
    results.mailboxes.length;
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <div class="search-heading">
      <div>
        <h2>搜索：${escapeHtml(results.query)}</h2>
        <span>${total} 条结果</span>
      </div>
      <button data-clear-search type="button">清空</button>
    </div>
    <div class="search-groups">
      ${searchContextResults(results.contexts)}
      ${searchEmailResults(results.emails)}
      ${searchSimpleResults("稿件", results.manuscripts, "manuscripts")}
      ${searchSimpleResults("邮箱", results.mailboxes, "mailboxes")}
    </div>
  `;
}

function searchContextResults(items) {
  const contexts = items.filter(contextVisibleInMain);
  if (!contexts.length) return "";
  return `
    <section class="search-group">
      <h3>业务脉络</h3>
      ${contexts
        .map(
          (item) => `
            <button class="search-result" data-open-context="${escapeHtml(item.context_id)}" type="button">
              <strong>${escapeHtml(displayTitle(item.title))}</strong>
              <span>${escapeHtml(item.customer_name || "")} · ${escapeHtml(item.email_count)} 封 · ${escapeHtml(humanText(item.suggested_action, ""))}</span>
            </button>
          `,
        )
        .join("")}
    </section>
  `;
}

function searchEmailResults(items) {
  if (!items.length) return "";
  return `
    <section class="search-group">
      <h3>邮件</h3>
      ${items
        .map((item) => {
          const context = emailContextForId(item.email_id);
          return `
            <button class="search-result" ${context ? `data-open-context="${escapeHtml(context.context_id)}"` : 'data-open-view="contexts"'} type="button">
              <strong>${escapeHtml(item.subject)}</strong>
              <span>${escapeHtml(item.customer_name || "")} · ${escapeHtml(item.received_at || "")} · ${escapeHtml(item.snippet || "")}</span>
            </button>
          `;
        })
        .join("")}
    </section>
  `;
}

function searchSimpleResults(label, items, viewName) {
  if (!items.length) return "";
  return `
    <section class="search-group">
      <h3>${escapeHtml(label)}</h3>
      ${items
        .map(
          (item) => `
            <button class="search-result" data-open-view="${escapeHtml(viewName)}" type="button">
              <strong>${escapeHtml(displayTitle(item.title || item.masked_email || item.manuscript_no || item.customer_name, ""))}</strong>
              <span>${escapeHtml(searchSimpleSubtitle(item, viewName))}</span>
            </button>
          `,
        )
        .join("")}
    </section>
  `;
}

function searchSimpleSubtitle(item, viewName) {
  if (viewName === "mailboxes") {
    return statusLabel(item.status);
  }
  const status = item.current_status ? workflowStatusText(item.current_status) : "";
  return [item.customer_name, status].filter(Boolean).join(" · ");
}

function auditActionText(action) {
  const labels = {
    confirm: "确认更新状态",
    confirm_no_action: "确认无需处理",
    request_revision: "选择待调整处理",
    handoff_revision_agent: "标记需手动处理",
    mark_revision_manual_required: "标记需手动处理",
    export_kingdee_csv: "生成金蝶导出",
    import_mailbox_credentials: "导入邮箱接入",
    test_mailbox_connection: "测试邮箱连接",
    fetch_mailbox_emails: "采集邮箱邮件",
  };
  return labels[action] || humanText(action, "操作记录");
}

function auditObjectText(objectType) {
  const labels = {
    review_task: "邮件判断任务",
    mailbox: "邮箱",
    export_batch: "导出批次",
  };
  return labels[objectType] || humanText(objectType, "对象");
}

function renderManuscripts() {
  const container = $("#manuscripts");
  if (!state.manuscripts.length) {
    container.innerHTML = `<div class="empty">暂无稿件档案。先确认审核任务即可生成事件流。</div>`;
    return;
  }
  container.innerHTML = state.manuscripts
    .map((item) => {
      const events = (item.events || [])
        .map(
          (event) => `
            <div class="event">
              ${escapeHtml(event.confirmed_at)} · ${escapeHtml(workflowStatusText(event.previous_status, "新建"))} -> ${escapeHtml(workflowStatusText(event.next_status))} · ${escapeHtml(event.confirmed_by)}
            </div>
          `,
        )
        .join("");
      return `
        <article class="record">
          <h3>${escapeHtml(displayTitle(item.title))}</h3>
          <div class="meta-row">
            <span>${escapeHtml(item.customer_name)}</span>
            <span>${escapeHtml(journalText(item.journal))}</span>
            <span>${escapeHtml(item.manuscript_no || "无编号")}</span>
            <span class="badge status">${escapeHtml(workflowStatusText(item.current_status))}</span>
          </div>
          <div class="meta-row">
            <span>负责人：${escapeHtml(item.owner || "未分配")}</span>
            <span>截止：${escapeHtml(item.due_date || "无")}</span>
          </div>
          <div class="event-list">${events || '<div class="event">暂无事件</div>'}</div>
        </article>
      `;
    })
    .join("");
}

function renderDaily() {
  const container = $("#dailyReport");
  const daily = state.daily || { summary: {}, events: [], open_reminders: [] };
  const summary = Object.entries(daily.summary)
    .map(([key, value]) => `<span class="badge status">${escapeHtml(workflowStatusText(key))}：${value}</span>`)
    .join(" ");
  const events = daily.events
    .map(
      (event) => `
        <article class="record">
          <h3>${escapeHtml(displayTitle(event.title))}</h3>
          <div class="meta-row">
            <span>${escapeHtml(event.customer_name)}</span>
            <span>${escapeHtml(journalText(event.journal))}</span>
            <span>${escapeHtml(event.manuscript_no || "无编号")}</span>
            <span class="badge status">${escapeHtml(workflowStatusText(event.next_status))}</span>
          </div>
        </article>
      `,
    )
    .join("");
  container.innerHTML = `
    <article class="record">
      <h3>统计汇总</h3>
      <div class="meta-row">${summary || "今日暂无正式事件"}</div>
      <div class="meta-row">开放提醒：${daily.open_reminders.length}</div>
    </article>
    ${events || '<div class="empty">今日暂无事件。</div>'}
  `;
}

function renderOpenReminders() {
  const container = $("#openReminders");
  if (!container) return;
  const reminders = state.daily?.open_reminders || [];
  if (!reminders.length) {
    container.innerHTML = `<div class="empty">暂无开放提醒。</div>`;
    return;
  }
  container.innerHTML = reminders
    .map(
      (reminder) => `
        <article class="record reminder-record">
          <div class="context-head">
            <div>
              <span class="badge warn">${escapeHtml(workflowStatusText(reminder.reminder_type, "跟进事项"))}</span>
              <h3>${escapeHtml(displayTitle(reminder.title || reminder.manuscript_no || "未匹配稿件"))}</h3>
            </div>
            <div class="context-counts">
              <span class="badge ${reminder.due_date ? "warn" : "status"}">${escapeHtml(reminder.due_date || "无截止日期")}</span>
              <span class="badge status">${escapeHtml(reminder.status === "open" ? "开放" : humanText(reminder.status))}</span>
            </div>
          </div>
          <div class="context-overview">
            ${contextFact("稿件编号", reminder.manuscript_no || "无编号")}
            ${contextFact("负责人", reminder.owner || "未分配")}
            ${contextFact("提醒类型", workflowStatusText(reminder.reminder_type, "跟进事项"))}
            ${contextFact("创建时间", reminder.created_at || "未知")}
          </div>
        </article>
      `,
    )
    .join("");
}

function renderSyncIssues() {
  const container = $("#syncIssues");
  if (!container) return;
  if (!state.syncIssues.length) {
    container.innerHTML = `<div class="empty">暂无同步异常。</div>`;
    return;
  }
  container.innerHTML = state.syncIssues
    .map(
      (item) => `
        <article class="record sync-record">
          <div class="context-head">
            <div>
              <span class="badge error">${escapeHtml(syncResultText(item.result))}</span>
              <h3>${escapeHtml(item.batch_no || `同步记录 #${item.sync_id}`)}</h3>
            </div>
            <div class="context-counts">
              <span class="badge status">${escapeHtml(item.sync_method || "未知方式")}</span>
              <span class="badge status">${escapeHtml(item.created_at || "未知时间")}</span>
            </div>
          </div>
          <div class="context-overview">
            ${contextFact("映射版本", item.mapping_version || "未知")}
            ${contextFact("操作人", item.operated_by || "未知")}
            ${contextFact("失败原因", item.failure_reason || "未记录")}
            ${contextFact("导出文件", item.exported_file || "无")}
          </div>
        </article>
      `,
    )
    .join("");
}

function syncResultText(result) {
  const labels = {
    failed: "失败",
    error: "异常",
    partial: "部分成功",
    success: "成功",
  };
  return labels[result] || humanText(result, "异常");
}

function statusLabel(status) {
  const labels = {
    active: "可用",
    auth_failed: "授权失败",
    needs_oauth: "需 OAuth",
    config_required: "待补配置",
    security_blocked: "安全拦截",
    test_failed: "测试失败",
    pending_test: "待测试",
  };
  return labels[status] || status || "未知";
}

function statusClass(status) {
  if (status === "active") return "ok";
  if (status === "needs_oauth" || status === "config_required" || status === "pending_test") return "warn";
  return "error";
}

function renderMailboxes() {
  const container = $("#mailboxes");
  if (!state.mailboxes.length) {
    container.innerHTML = `<div class="empty">暂无邮箱接入记录。</div>`;
    return;
  }
  const counts = state.mailboxes.reduce((acc, item) => {
    acc[item.status] = (acc[item.status] || 0) + 1;
    return acc;
  }, {});
  const summary = Object.entries(counts)
    .map(([key, value]) => `<span class="badge ${statusClass(key)}">${statusLabel(key)}：${value}</span>`)
    .join(" ");
  const rows = state.mailboxes
    .map(
      (item) => `
        <article class="record mailbox-record">
          <h3>${escapeHtml(item.masked_email)}</h3>
          <div class="meta-row">
            <span class="badge ${statusClass(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
            <span>${escapeHtml(item.last_provider || "未识别服务商")}</span>
            <span>${escapeHtml(item.auth_method || "未知认证")}</span>
            <span>项目：${escapeHtml(item.project_count || 0)}</span>
          </div>
          <div class="fields">
            ${field("最后测试", item.last_tested_at || "未测试")}
            ${field("收件箱邮件数", item.inbox_message_count ?? "无")}
            ${field("测试结果", item.last_test_result || "无")}
            ${field("错误类型", item.last_error_type || item.error_reason || "无")}
            ${item.oauth_linked ? field("OAuth", `已授权 ${item.oauth_account_hint || ""}`) : ""}
          </div>
          ${microsoftAction(item)}
        </article>
      `,
    )
    .join("");
  container.innerHTML = `
    ${microsoftOAuthPanel()}
    <article class="record">
      <h3>接入汇总</h3>
      <div class="meta-row">${summary}</div>
    </article>
    ${rows}
  `;
}

function revisionJobById(jobId) {
  return state.revisionJobs.find((job) => String(job.job_id) === String(jobId));
}

function revisionTargetLabel(job) {
  return job.manuscript_no || job.project_code || displayTitle(job.title, "未匹配稿件");
}

function renderRevisionJobs() {
  const container = $("#revisionJobs");
  if (!container) return;
  if (!state.revisionJobs.length) {
    container.innerHTML = `<div class="empty">暂无返修处理邮件。识别到“要求返修/修改稿件”后会出现在这里。</div>`;
    return;
  }
  container.innerHTML = state.revisionJobs
    .map((job) => {
      const subject = job.subject_translated || job.subject || "无主题";
      return `
        <article class="record revision-record">
          <div class="context-head">
            <div>
              <span class="badge warn">返修处理</span>
              <h3>${escapeHtml(revisionTargetLabel(job))}</h3>
            </div>
            <div class="context-counts">
              <span class="badge status">${escapeHtml(workflowStatusText("Revision Requested"))}</span>
              <span class="badge ${job.task_status === "pending" ? "warn" : "status"}">${escapeHtml(taskStatusText(job.task_status))}</span>
            </div>
          </div>
          <div class="context-overview">
            ${contextFact("客户", job.customer_name || "未知客户")}
            ${contextFact("作者", job.author_name || "未知作者")}
            ${contextFact("项目/稿件", revisionTargetLabel(job))}
            ${contextFact("截止日期", job.due_date || "未识别")}
          </div>
          <div class="revision-summary">
            <div>
              <span>邮件主题</span>
              <p>${escapeHtml(humanText(subject, "无主题"))}</p>
            </div>
            <div>
              <span>修改意见摘录</span>
              <p>${escapeHtml(humanText(job.body_translated_excerpt || job.revision_instructions || job.body_excerpt, "暂无摘录"))}</p>
            </div>
          </div>
          <div class="task-actions">
            ${
              job.task_id && job.task_status === "pending"
                ? `<button class="primary" data-process-task="${escapeHtml(job.task_id)}" type="button">处理</button>`
                : `<span class="badge warn">功能尚未完成，需手动处理</span>`
            }
          </div>
        </article>
      `;
    })
    .join("");
}

function taskStatusText(status) {
  const labels = {
    pending: "待判断",
    confirmed: "已处理",
    revision_handoff: "需手动处理",
    revision_manual_required: "需手动处理",
    needs_revision: "待调整",
    unreviewed: "未生成任务",
  };
  return labels[status] || humanText(status, "未知状态");
}

function microsoftOAuthPanel() {
  const oauth = state.microsoftOAuth || {};
  return `
    <article class="record">
      <h3>Microsoft OAuth</h3>
      <div class="meta-row">
        <span class="badge ${oauth.configured ? "ok" : "warn"}">${oauth.configured ? "已配置" : "未配置"}</span>
        <span>已授权：${escapeHtml(oauth.linked_mailboxes || 0)}</span>
        <span>待授权：${escapeHtml(oauth.pending_mailboxes || 0)}</span>
      </div>
      <div class="fields">
        ${field("回调地址", oauth.redirect_uri || "未配置")}
        ${field("Scopes", oauth.scopes || "未配置")}
      </div>
    </article>
  `;
}

function microsoftAction(item) {
  if (item.auth_method !== "oauth2") return "";
  if (item.oauth_linked && item.status === "active") {
    return `<div class="task-actions"><span class="badge ok">OAuth 已验证</span></div>`;
  }
  if (!(state.microsoftOAuth || {}).configured) {
    return `<div class="task-actions"><span class="badge warn">等待 OAuth 配置</span></div>`;
  }
  return `
    <div class="task-actions">
      <button class="primary" data-microsoft-oauth="${escapeHtml(item.mailbox_id)}" type="button">Microsoft 授权</button>
    </div>
  `;
}

function renderAudit() {
  const container = $("#auditLogs");
  if (!state.auditLogs.length) {
    container.innerHTML = `<div class="empty">暂无审计日志。</div>`;
    return;
  }
  container.innerHTML = state.auditLogs
    .map(
      (log) => `
        <article class="record">
          <h3>${escapeHtml(auditActionText(log.action))} · ${escapeHtml(auditObjectText(log.object_type))} #${escapeHtml(log.object_id)}</h3>
          <div class="meta-row">
            <span>${escapeHtml(log.actor)}</span>
            <span>${escapeHtml(log.created_at)}</span>
          </div>
        </article>
      `,
    )
    .join("");
}

async function handleFetchEmails() {
  const result = await api("/api/jobs/fetch-emails", { method: "POST", body: "{}" });
  showToast(`已生成 ${result.created_review_tasks} 个审核任务`);
  await loadAll();
}

async function handleExportKingdee() {
  const result = await api("/api/exports/kingdee-csv", {
    method: "POST",
    body: JSON.stringify({ operated_by: "业务审核员" }),
  });
  showToast(`已生成金蝶 CSV：${result.batch_no}`);
  await loadAll();
}

async function handleConfirmTask(taskId) {
  const result = await api(`/api/review-tasks/${taskId}/confirm`, {
    method: "POST",
    body: JSON.stringify({ confirmed_by: "业务审核员", note: "前端确认并更新状态" }),
  });
  closeModal();
  showToast(result.result === "no_action" ? "已确认无需处理" : `已更新稿件状态：${workflowStatusText(result.current_status)}`);
  await loadAll();
}

async function handleRevisionManualRequired(taskId) {
  const result = await api(`/api/revision-jobs/${taskId}/manual-required`, {
    method: "POST",
    body: JSON.stringify({
      reviewed_by: "业务审核员",
      note: "功能尚未完成，需手动处理",
    }),
  });
  closeModal();
  showToast(result.message || "功能尚未完成，需手动处理");
  await loadAll();
}

async function handleReviseTask(taskId) {
  await api(`/api/review-tasks/${taskId}/revise`, {
    method: "POST",
    body: JSON.stringify({ reviewed_by: "业务审核员", reason: "选择待调整处理，需人工复核" }),
  });
  closeModal();
  showToast("已标记为待调整");
  await loadAll();
}

async function handleMicrosoftOAuth(mailboxId) {
  const result = await api("/api/oauth/microsoft/start", {
    method: "POST",
    body: JSON.stringify({ mailbox_id: Number(mailboxId) }),
  });
  window.open(result.auth_url, "_blank", "noopener,noreferrer");
  showToast("已打开 Microsoft 授权页面");
}

async function handleLogout() {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  window.location.href = "/login";
}

function bindEvents() {
  $("#fetchEmailsBtn").addEventListener("click", () => handleFetchEmails().catch((error) => showToast(error.message)));
  $("#exportKingdeeBtn").addEventListener("click", () => handleExportKingdee().catch((error) => showToast(error.message)));
  $("#logoutBtn").addEventListener("click", () => handleLogout().catch((error) => showToast(error.message)));
  $("#globalSearchForm").addEventListener("submit", (event) => handleSearch(event).catch((error) => showToast(error.message)));
  $("#modalBackdrop").addEventListener("click", (event) => {
    if (event.target.id === "modalBackdrop") {
      closeModal();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#modalBackdrop").classList.contains("hidden")) {
      closeModal();
    }
  });
  document.body.addEventListener("click", (event) => {
    const modalCloseButton = event.target.closest("[data-close-modal], #modalCloseBtn");
    if (modalCloseButton) {
      closeModal();
      return;
    }
    const executeConfirmButton = event.target.closest("[data-execute-confirm]");
    if (executeConfirmButton) {
      handleConfirmTask(executeConfirmButton.dataset.executeConfirm).catch((error) => showToast(error.message));
      return;
    }
    const executeRevisionManualButton = event.target.closest("[data-execute-revision-manual]");
    if (executeRevisionManualButton) {
      handleRevisionManualRequired(executeRevisionManualButton.dataset.executeRevisionManual).catch((error) => showToast(error.message));
      return;
    }
    const executeReviseButton = event.target.closest("[data-execute-revise]");
    if (executeReviseButton) {
      handleReviseTask(executeReviseButton.dataset.executeRevise).catch((error) => showToast(error.message));
      return;
    }
    const modalViewButton = event.target.closest("[data-open-view-from-modal]");
    if (modalViewButton) {
      closeModal();
      openView(modalViewButton.dataset.openViewFromModal);
      return;
    }
    const processButton = event.target.closest("[data-process-task]");
    if (processButton) {
      openProcessModal(processButton.dataset.processTask);
      return;
    }
    const metricButton = event.target.closest("[data-metric-view]");
    if (metricButton) {
      openView(metricButton.dataset.metricView);
      return;
    }
    const microsoftButton = event.target.closest("[data-microsoft-oauth]");
    if (microsoftButton) {
      handleMicrosoftOAuth(microsoftButton.dataset.microsoftOauth).catch((error) => showToast(error.message));
      return;
    }
    const fetchButton = event.target.closest("[data-fetch-emails]");
    if (fetchButton) {
      handleFetchEmails().catch((error) => showToast(error.message));
      return;
    }
    const exportButton = event.target.closest("[data-export-kingdee]");
    if (exportButton) {
      handleExportKingdee().catch((error) => showToast(error.message));
      return;
    }
    const openViewButton = event.target.closest("[data-open-view]");
    if (openViewButton) {
      openView(openViewButton.dataset.openView);
      return;
    }
    const openContextButton = event.target.closest("[data-open-context]");
    if (openContextButton) {
      openContext(openContextButton.dataset.openContext);
      return;
    }
    const clearSearchButton = event.target.closest("[data-clear-search]");
    if (clearSearchButton) {
      state.searchQuery = "";
      state.searchResults = null;
      $("#globalSearchInput").value = "";
      renderSearchResults();
      return;
    }
    const filter = event.target.closest("[data-task-filter]");
    if (filter) {
      setTaskFilter(filter.dataset.taskFilter);
      return;
    }
    const tab = event.target.closest(".tab");
    if (tab) {
      openView(tab.dataset.view);
    }
  });
}

function setTaskFilter(filterName) {
  state.taskFilter = filterName;
  document.querySelectorAll(".filter").forEach((node) => node.classList.toggle("active", node.dataset.taskFilter === filterName));
  renderTasks();
}

function openView(viewName) {
  document.querySelectorAll(".tab").forEach((node) => node.classList.toggle("active", node.dataset.view === viewName));
  document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
  const view = $(`#${viewName}View`);
  if (view) {
    view.classList.add("active");
    view.scrollIntoView({ behavior: "auto", block: "start" });
  }
}

bindEvents();
loadAll().catch((error) => showToast(error.message));
