const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const kinds = {
  natal: "本命盘",
  decade: "大运 / 童限",
  year: "流年盘",
  date: "日期流盘",
  atlas: "人生图谱",
};
const sources = {
  group: "群友起盘",
  web: "后台排盘",
  legacy: "历史档案",
  binding: "已绑定命盘",
  binding_import: "历史绑定补档",
};
const labels = {
  day: "白天星图",
  night: "夜间星空",
  image: "图片",
  text: "文字",
  current: "按本月",
  next: "按下月",
  split: "十五 / 十六分界",
  next_day: "23 时换日",
  midnight: "零点换日",
  qs: "全书 · 七级",
  zz: "中州 · 五级",
  xd1: "现代 · 七级",
  xd2: "现代 · 五级",
  year: "按生年 / 年干",
  month: "按安星月",
  palace: "按宫位",
  water: "水土共长生",
  fire: "火土共长生",
};
let settingsState = null,
  page = 1,
  pages = 1,
  queryGeneration = 0,
  ready = false;
let pendingSubmission = null;
const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}
function notice(message, kind = "") {
  $("notice").textContent = message;
  $("notice").className = "notice " + kind;
}
async function action(button, task, busy = "处理中…") {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = busy;
  try {
    return await task();
  } catch (error) {
    notice(error.message || "操作失败，请稍后重试。", "error");
  } finally {
    button.disabled = !ready;
    button.textContent = original;
  }
}
function changeTab(name) {
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const selected = button.dataset.tab === name;
    button.setAttribute("aria-pressed", String(selected));
    $("tab-" + button.dataset.tab).hidden = !selected;
  });
  if (ready && $("notice").classList.contains("error"))
    notice("已连接管理后台。");
}
document
  .querySelectorAll("[data-tab]")
  .forEach((button) =>
    button.addEventListener("click", () => changeTab(button.dataset.tab)),
  );

function chartArgs() {
  const args = [$("calendar").value, $("birth").value.trim(), $("sex").value];
  if ($("mode").value === "decade") args.push("运限=" + $("decade").value);
  if ($("mode").value === "year")
    args.push("流年=" + $("flow-year").value.trim());
  if ($("mode").value === "date") {
    const target = $("flow-date").value.trim();
    args.push("流盘=" + target.slice(0, 8), "流时=" + target.slice(8));
  }
  if ($("theme").value) args.push("主题=" + $("theme").value);
  if ($("extra").value.trim()) args.push($("extra").value.trim());
  return args.join(" ");
}
function updateCommand() {
  const mode = $("mode").value;
  for (const item of ["decade", "year", "date"])
    $(item + "-field").hidden = mode !== item;
  $("flow-year").required = mode === "year";
  $("flow-date").required = mode === "date";
  $("lunar-hint").hidden = $("calendar").value !== "农历";
  $("command-preview").textContent = $("birth").value.trim()
    ? "/紫微 " + chartArgs()
    : "填写出生资料后显示";
}
$("chart-form").addEventListener("input", updateCommand);
$("chart-form").addEventListener("change", updateCommand);
$("demo").addEventListener("click", () => {
  $("birth").value = "199006150830";
  $("sex").value = "男";
  $("calendar").value = "公历";
  $("mode").value = "natal";
  $("extra").value = "";
  updateCommand();
  if (ready) notice("已填入虚构演示资料，可直接生成命盘。");
});
function requestId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join(
    "",
  );
}
function renderResult(target, record) {
  target.replaceChildren();
  const heading = node("div", undefined, "result-header");
  const title = node("div");
  title.append(
    node("h2", kinds[record.kind]),
    node(
      "p",
      dateFormat.format(new Date(record.created * 1000)) +
        " · " +
        sources[record.source],
    ),
  );
  heading.append(
    title,
    node("span", record.summary.sex + " · " + record.summary.bureau, "badge"),
  );
  target.append(heading);
  target.append(node("p", "编号 " + record.id, "help-text"));
  if (record.user_id)
    target.append(
      node(
        "p",
        `所属群友：${record.user_id} · ${record.platform_id}`,
        "help-text",
      ),
    );
  if (record.warning) target.append(node("p", record.warning, "notice"));
  if (record.image && record.image.startsWith("data:image/png;base64,")) {
    const actions = node("div", undefined, "result-actions");
    const download = node("a", "保存图片", "download");
    download.href = record.image;
    download.download = "ziwei-" + record.id + ".png";
    actions.append(download);
    if (target.id === "chart-result") {
      const expand = node("button", "查看大图", "secondary");
      expand.type = "button";
      expand.addEventListener("click", () => {
        renderResult($("detail-result"), record);
        $("detail-dialog").showModal();
      });
      actions.append(expand);
    }
    const img = node("img", undefined, "chart-image");
    img.src = record.image;
    img.alt = kinds[record.kind] + "，出生时间 " + record.summary.civil;
    target.append(actions, img);
  }
  const details = node("details");
  details.open = !record.image;
  details.append(
    node("summary", "查看文字盘"),
    node("pre", record.text, "text-chart"),
  );
  target.append(details);
}
$("chart-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!ready || $("create").disabled) return;
  action(
    $("create"),
    async () => {
      if (
        $("mode").value === "date" &&
        !/^\d{12}$/.test($("flow-date").value.trim())
      )
        throw new Error("目标日期时间须连写 12 位。");
      const args = chartArgs();
      if (!pendingSubmission || pendingSubmission.args !== args)
        pendingSubmission = { args, request_id: requestId() };
      const result = await bridge.apiPost("manage/charts", pendingSubmission);
      pendingSubmission = null;
      renderResult($("chart-result"), result);
      notice("命盘已生成并保存，可在起盘记录中回看。", "success");
      await loadOverview();
      await loadRecords(1);
    },
    "正在排盘…",
  );
});
$("close-detail").addEventListener("click", () => $("detail-dialog").close());

async function loadOverview() {
  const state = await bridge.apiGet("manage/overview");
  $("stat-group").textContent = state.counts.group || 0;
  $("stat-users").textContent = state.users;
  $("stat-web").textContent = state.counts.web || 0;
  $("stat-legacy").textContent =
    (state.counts.legacy || 0) + (state.counts.binding_import || 0);
  const selected = $("filter-group").value;
  $("filter-group").replaceChildren(new Option("全部群", ""));
  for (const platform of state.platforms)
    $("filter-group").add(
      new Option(platform + " · 全部群", JSON.stringify([platform, ""])),
    );
  state.groups.forEach((group) =>
    $("filter-group").add(
      new Option(
        group.group_id + " · " + group.platform_id,
        JSON.stringify([group.platform_id, group.group_id]),
      ),
    ),
  );
  $("filter-group").value = Array.from($("filter-group").options).some(
    (option) => option.value === selected,
  )
    ? selected
    : "";
}
async function loadRecords(nextPage = 1) {
  const generation = ++queryGeneration;
  const group = $("filter-group").value
    ? JSON.parse($("filter-group").value)
    : ["", ""];
  const result = await bridge.apiGet("manage/records", {
    page: nextPage,
    source: $("filter-source").value,
    kind: $("filter-kind").value,
    platform: group[0],
    group: group[1],
    user: $("filter-user").value.trim(),
    search: $("filter-search").value.trim(),
    start: $("filter-start").value,
    end: $("filter-end").value,
  });
  if (generation !== queryGeneration) return;
  page = result.page;
  pages = result.pages;
  $("record-total").textContent = "共 " + result.total + " 条";
  $("page-info").textContent = page + " / " + pages;
  $("previous").disabled = page <= 1;
  $("next").disabled = page >= pages;
  $("record-rows").replaceChildren();
  result.items.forEach((record) => {
    const row = node("tr");
    row.append(node("td", dateFormat.format(new Date(record.created * 1000))));
    const person = node("td");
    person.append(
      node(
        "div",
        record.user_name || record.user_id || record.actor || "未记录",
        "identity",
      ),
    );
    person.append(
      node(
        "small",
        record.user_name
          ? record.user_id + " · " + sources[record.source]
          : sources[record.source],
      ),
    );
    const groupCell = node("td", record.group_id || "—");
    groupCell.append(
      node(
        "small",
        record.platform_id ||
          (record.source === "legacy" ? "身份信息未保存" : "管理页面"),
      ),
    );
    const chart = node("td", kinds[record.kind] + " · " + record.summary.sex);
    if (record.source === "web" && record.user_id)
      person.append(node("small", "后台操作：" + record.actor));
    if (record.summary.binding_action)
      chart.append(
        node(
          "small",
          record.summary.binding_action === "replace" ? "换绑排盘" : "绑定排盘",
        ),
      );
    chart.append(node("small", record.summary.civil));
    if (record.summary.target)
      chart.append(node("small", "目标：" + record.summary.target));
    if (record.summary.decade !== null)
      chart.append(
        node(
          "small",
          record.summary.decade === 0
            ? "童限"
            : "第 " + record.summary.decade + " 大运",
        ),
      );
    const operation = node("td");
    const view = node("button", "查看命盘", "secondary");
    view.type = "button";
    view.addEventListener("click", () =>
      action(
        view,
        async () => {
          const detail = await bridge.apiGet("manage/records/" + record.id);
          renderResult($("detail-result"), detail);
          $("detail-dialog").showModal();
        },
        "加载中…",
      ),
    );
    operation.append(view);
    row.append(person, groupCell, chart, operation);
    $("record-rows").append(row);
  });
  if (!result.items.length) {
    const row = node("tr"),
      cell = node("td", "没有符合条件的起盘记录。", "no-records");
    cell.colSpan = 5;
    row.append(cell);
    $("record-rows").append(row);
  }
}
$("filter-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, () => loadRecords(1), "查询中…");
});
$("reset-filters").addEventListener("click", () => {
  $("filter-form").reset();
  action($("reset-filters"), () => loadRecords(1));
});
$("previous").addEventListener("click", async () => {
  await action($("previous"), () => loadRecords(page - 1));
  $("previous").disabled = page <= 1;
});
$("next").addEventListener("click", async () => {
  await action($("next"), () => loadRecords(page + 1));
  $("next").disabled = page >= pages;
});

function renderSettings(state) {
  settingsState = state;
  $("basic-settings").replaceChildren();
  $("rule-settings").replaceChildren();
  for (const [key, item] of Object.entries(state.schema)) {
    const label = node("label", item.description);
    const input = node(Array.isArray(item.options) ? "select" : "input");
    input.id = "setting-" + key;
    const fieldLabels = {
      tianma: { year: "按年支", month: "按安星月" },
      life_master: { palace: "按命宫地支", year: "按生年地支" },
      annual_hua: { year: "流年年干", palace: "流年命宫宫干" },
    };
    if (Array.isArray(item.options))
      item.options.forEach((value) =>
        input.add(
          new Option(
            fieldLabels[key]?.[value] || labels[value] || String(value),
            String(value),
          ),
        ),
      );
    else if (item.type === "bool") {
      input.type = "checkbox";
      input.checked = state.values[key];
      label.className = "setting-check";
    } else if (["int", "float"].includes(item.type)) {
      input.type = "number";
      input.step = item.type === "int" ? "1" : "any";
      input.required = true;
    } else input.type = "text";
    if (item.type !== "bool") input.value = state.values[key];
    if (item.type === "bool") label.prepend(input);
    else label.append(input);
    const field = node("div");
    field.append(label);
    if (item.hint || typeof item.options === "string")
      label.append(node("small", item.hint || item.options));
    const basic = [
      "image_theme",
      "output_mode",
      "font_path",
      "timezone",
      "longitude",
      "true_solar",
    ].includes(key);
    $(basic ? "basic-settings" : "rule-settings").append(field);
  }
  $("save-settings").disabled = !state.can_save;
  $("settings-note").textContent = !state.can_save
    ? "当前环境不支持保存，请使用 AstrBot 插件配置页。"
    : !state.image_available
      ? "未找到可用中文字体，当前会返回文字盘。"
      : "保存到 AstrBot 插件配置，重载后仍然保留。";
}
async function loadSettings() {
  renderSettings(await bridge.apiGet("manage/settings"));
}
$("settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (
    !ready ||
    !settingsState ||
    !settingsState.can_save ||
    $("save-settings").disabled
  )
    return;
  action(
    $("save-settings"),
    async () => {
      const values = {};
      for (const [key, item] of Object.entries(settingsState.schema)) {
        const input = $("setting-" + key);
        values[key] =
          item.type === "bool"
            ? input.checked
            : ["int", "float"].includes(item.type)
              ? Number(input.value)
              : input.value;
      }
      const result = await bridge.apiPost("manage/settings/save", {
        values,
        revision: settingsState.revision,
      });
      renderSettings(result);
      notice("配置已保存，后续新建命盘立即使用。", "success");
    },
    "正在保存…",
  );
});
$("reload-settings").addEventListener("click", () =>
  action($("reload-settings"), loadSettings),
);

let bindingState = null,
  bindingPage = 1,
  bindingPages = 1,
  bindingGeneration = 0,
  bindingQueryGeneration = 0,
  bindingBusy = false,
  bindingSubmission = null;
let bindingHistoryPage = 1,
  bindingHistoryPages = 1,
  bindingHistoryGeneration = 0;
function bindingIdentity() {
  return {
    platform_id: $("binding-platform").value.trim(),
    user_id: $("binding-user").value.trim(),
  };
}
function sameBindingTarget(state) {
  const target = bindingIdentity();
  return (
    state &&
    target.platform_id === state.platform_id &&
    target.user_id === state.user_id
  );
}
function bindingButtons() {
  const loaded = ready && sameBindingTarget(bindingState) && !bindingBusy;
  $("binding-save").disabled = !loaded;
  $("binding-save").textContent = bindingState?.active
    ? "换绑为此命盘"
    : "绑定命盘";
  $("binding-unbind").disabled = !loaded || !bindingState.active;
  $("binding-view").disabled = !loaded || !bindingState.active;
  $("binding-user-records").disabled = !loaded;
}
function showBindingState(state) {
  bindingState = state;
  const info = state.active
    ? `已绑定：${state.summary.civil} · ${state.summary.sex}。换绑将替换此命盘。`
    : "尚未绑定，请填写出生资料后绑定。";
  $("binding-state").textContent =
    `${state.user_id} · ${info}` +
    (state.updated
      ? ` 最近操作：${dateFormat.format(new Date(state.updated * 1000))}（${state.via === "admin" ? "后台：" + state.actor : "群友本人"}）`
      : "");
  bindingButtons();
}
function invalidateBinding() {
  bindingGeneration++;
  bindingState = null;
  bindingSubmission = null;
  bindingHistoryGeneration++;
  bindingHistoryPage = 1;
  $("binding-history-list").replaceChildren(
    node("li", "请先载入群友。", "help-text"),
  );
  $("binding-history-owner").textContent =
    "载入群友后，可回看每次绑定、换绑和解绑。";
  $("binding-history-total").textContent = "未载入";
  $("binding-history-page").textContent = "—";
  $("binding-history-previous").disabled = $("binding-history-next").disabled =
    true;
  $("binding-state").textContent = "账号已变化，请重新载入该群友。";
  $("binding-result").replaceChildren(
    node("p", "载入群友后可查看绑定盘。", "help-text"),
  );
  bindingButtons();
}
for (const id of ["binding-platform", "binding-user"])
  $(id).addEventListener("input", invalidateBinding);
async function loadBinding(render = false) {
  const target = bindingIdentity();
  if (!target.platform_id || !target.user_id)
    throw new Error("请填写机器人实例和群友账号。");
  const generation = ++bindingGeneration;
  const result = await bridge.apiGet("manage/bindings/target", {
    ...target,
    render: render ? "1" : "0",
  });
  if (generation !== bindingGeneration || !sameBindingTarget(result)) return;
  showBindingState(result);
  if (result.preview) renderResult($("binding-result"), result.preview);
  else
    $("binding-result").replaceChildren(
      node(
        "p",
        result.active
          ? "已载入，点击“查看绑定盘”显示图片。"
          : "该群友尚未绑定命盘。",
        "help-text",
      ),
    );
  await loadBindingHistory(render ? bindingHistoryPage : 1);
}
async function loadBindingHistory(nextPage = 1) {
  if (!sameBindingTarget(bindingState)) return;
  const target = bindingIdentity(),
    generation = ++bindingHistoryGeneration;
  const result = await bridge.apiGet("manage/bindings/history", {
    ...target,
    page: nextPage,
  });
  if (generation !== bindingHistoryGeneration || !sameBindingTarget(result))
    return;
  bindingHistoryPage = result.page;
  bindingHistoryPages = result.pages;
  $("binding-history-total").textContent = `共 ${result.total} 次操作`;
  $("binding-history-owner").textContent =
    `${result.platform_id} · ${result.user_id} 的绑定历史，每次命盘均保留。`;
  $("binding-history-page").textContent = `${result.page} / ${result.pages}`;
  $("binding-history-previous").disabled = result.page <= 1;
  $("binding-history-next").disabled = result.page >= result.pages;
  $("binding-history-list").replaceChildren();
  const labels = { bind: "绑定命盘", replace: "更换绑定", unbind: "解除绑定" };
  const summary = (value) =>
    value ? `${value.civil} · ${value.sex}` : "旧版命盘资料未保存";
  for (const entry of result.items) {
    const item = node("li", undefined, "binding-history-item");
    item.dataset.action = entry.action;
    const info = node("div", undefined, "history-info"),
      actions = node("div", undefined, "history-actions");
    info.append(
      node(
        "h3",
        labels[entry.action] +
          " · " +
          dateFormat.format(new Date(entry.created * 1000)),
      ),
    );
    info.append(
      node(
        "p",
        entry.action === "replace"
          ? `原：${summary(entry.previous_summary)} → 新：${summary(entry.summary)}`
          : summary(entry.summary),
      ),
    );
    info.append(
      node(
        "p",
        (entry.via === "admin" ? "后台操作：" : "本人操作：") +
          entry.actor +
          (entry.group_id ? " · 群 " + entry.group_id : "") +
          (entry.restored ? " · 旧版记录" : ""),
        "help-text",
      ),
    );
    function view(label, id) {
      const button = node("button", label, "secondary");
      button.type = "button";
      button.addEventListener("click", () =>
        action(button, async () => {
          renderResult(
            $("detail-result"),
            await bridge.apiGet("manage/records/" + id),
          );
          $("detail-dialog").showModal();
        }),
      );
      actions.append(button);
    }
    if (entry.available)
      view(
        entry.action === "unbind" ? "查看解绑前命盘" : "查看当时命盘",
        entry.record_id,
      );
    if (entry.action === "replace" && entry.previous_available)
      view("查看换绑前命盘", entry.previous_record);
    item.append(info, actions);
    $("binding-history-list").append(item);
  }
  if (!result.items.length)
    $("binding-history-list").append(
      node("li", "该群友暂无绑定操作记录。", "help-text"),
    );
}
async function viewUserRecords() {
  if (!sameBindingTarget(bindingState)) throw new Error("请先载入该群友。");
  const target = bindingIdentity(),
    value = JSON.stringify([target.platform_id, ""]);
  $("filter-form").reset();
  if (
    !Array.from($("filter-group").options).some(
      (option) => option.value === value,
    )
  )
    $("filter-group").add(new Option(target.platform_id + " · 全部群", value));
  $("filter-group").value = value;
  $("filter-user").value = target.user_id;
  changeTab("records");
  await loadRecords(1);
}
async function loadBindings(nextPage = 1) {
  const generation = ++bindingQueryGeneration;
  const result = await bridge.apiGet("manage/bindings", {
    page: nextPage,
    search: $("binding-search-text").value.trim(),
    platform: $("binding-filter-platform").value,
  });
  if (generation !== bindingQueryGeneration) return;
  bindingPage = result.page;
  bindingPages = result.pages;
  $("binding-total").textContent = `${result.total} 位群友`;
  $("binding-page").textContent = `${bindingPage} / ${bindingPages}`;
  $("binding-previous").disabled = bindingPage <= 1;
  $("binding-next").disabled = bindingPage >= bindingPages;
  const selected = $("binding-filter-platform").value;
  $("binding-filter-platform").replaceChildren(new Option("全部实例", ""));
  $("binding-platforms").replaceChildren();
  for (const platform of result.platforms) {
    $("binding-filter-platform").add(new Option(platform, platform));
    $("binding-platforms").append(new Option(platform, platform));
  }
  $("binding-filter-platform").value = selected;
  $("binding-rows").replaceChildren();
  for (const user of result.items) {
    const row = node("tr"),
      person = node("td", user.user_name || "未记录昵称"),
      state = node("td", user.active ? "已绑定" : "未绑定"),
      operation = node("td");
    person.append(node("small", user.user_id));
    if (user.updated)
      state.append(
        node("small", dateFormat.format(new Date(user.updated * 1000))),
      );
    const select = node("button", "管理", "secondary");
    select.type = "button";
    select.addEventListener("click", () =>
      action(select, async () => {
        $("binding-platform").value = user.platform_id;
        $("binding-user").value = user.user_id;
        invalidateBinding();
        await loadBinding();
        $("binding-form").scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }),
    );
    operation.append(select);
    row.append(person, node("td", user.platform_id), state, operation);
    $("binding-rows").append(row);
  }
  if (!result.items.length) {
    const row = node("tr"),
      cell = node(
        "td",
        "暂无群友记录，也可以在下方填写账号后绑定。",
        "no-records",
      );
    cell.colSpan = 4;
    row.append(cell);
    $("binding-rows").append(row);
  }
}
async function saveBinding(unbind = false) {
  if (!sameBindingTarget(bindingState)) throw new Error("请先载入该群友。");
  const body = {
    ...bindingIdentity(),
    action: unbind ? "unbind" : bindingState.active ? "replace" : "bind",
    revision: bindingState.revision,
    args: !unbind
      ? [
          $("binding-calendar").value,
          $("binding-birth").value.trim(),
          $("binding-sex").value,
          $("binding-extra").value.trim(),
        ]
          .filter(Boolean)
          .join(" ")
      : "",
  };
  const signature = JSON.stringify(body);
  if (!bindingSubmission || bindingSubmission.signature !== signature)
    bindingSubmission = {
      signature,
      body: { ...body, request_id: requestId() },
    };
  bindingBusy = true;
  bindingButtons();
  try {
    const state = await bridge.apiPost(
      "manage/bindings/save",
      bindingSubmission.body,
    );
    bindingSubmission = null;
    if (sameBindingTarget(state)) {
      showBindingState(state);
      $("binding-result").replaceChildren(
        node(
          "p",
          state.active
            ? "绑定已保存，点击“查看绑定盘”查看图片。"
            : "已解除绑定，历史记录仍保留。",
          "help-text",
        ),
      );
    }
    notice(
      state.replayed
        ? "该请求已处理，已刷新当前绑定状态。"
        : unbind
          ? "已解除绑定，历史记录仍保留。"
          : "绑定已保存，同一机器人内各已开启的群通用。",
      "success",
    );
    await Promise.all([
      loadBindings(bindingPage),
      loadOverview(),
      loadRecords(page),
      sameBindingTarget(state) ? loadBindingHistory(1) : Promise.resolve(),
    ]);
  } finally {
    bindingBusy = false;
    bindingButtons();
  }
}
$("binding-search").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, () => loadBindings(1));
});
$("binding-user-records").addEventListener("click", async () => {
  await action($("binding-user-records"), viewUserRecords);
  bindingButtons();
});
$("binding-history-previous").addEventListener("click", async () => {
  await action($("binding-history-previous"), () =>
    loadBindingHistory(bindingHistoryPage - 1),
  );
  $("binding-history-previous").disabled = bindingHistoryPage <= 1;
});
$("binding-history-next").addEventListener("click", async () => {
  await action($("binding-history-next"), () =>
    loadBindingHistory(bindingHistoryPage + 1),
  );
  $("binding-history-next").disabled =
    bindingHistoryPage >= bindingHistoryPages;
});
$("binding-load").addEventListener("click", () =>
  action($("binding-load"), () => loadBinding()),
);
$("binding-view").addEventListener("click", async () => {
  await action($("binding-view"), () => loadBinding(true));
  bindingButtons();
});
$("binding-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!ready || $("binding-save").disabled) return;
  await action($("binding-save"), () => saveBinding());
  bindingButtons();
});
$("binding-unbind").addEventListener("click", async () => {
  if ($("binding-unbind").disabled) return;
  await action($("binding-unbind"), () => saveBinding(true));
  bindingButtons();
});
$("binding-previous").addEventListener("click", async () => {
  await action($("binding-previous"), () => loadBindings(bindingPage - 1));
  $("binding-previous").disabled = bindingPage <= 1;
});
$("binding-next").addEventListener("click", async () => {
  await action($("binding-next"), () => loadBindings(bindingPage + 1));
  $("binding-next").disabled = bindingPage >= bindingPages;
});
$("refresh").addEventListener("click", () =>
  action($("refresh"), async () => {
    await Promise.all([
      loadOverview(),
      loadRecords(page),
      loadBindings(bindingPage),
      loadBindingHistory(bindingHistoryPage),
    ]);
    notice("记录已刷新。", "success");
  }),
);
try {
  if (!bridge)
    throw new Error(
      "请从 AstrBot WebUI → 插件 → 紫微斗数排盘 → 紫微排盘控制台打开。",
    );
  await bridge.ready();
  await Promise.all([
    loadSettings(),
    loadOverview(),
    loadRecords(),
    loadBindings(),
  ]);
  ready = true;
  $("create").disabled = false;
  notice("已连接管理后台。群友起盘会自动归档，页面排盘也会保留记录。");
} catch (error) {
  notice(error.message || "连接失败，请刷新页面。", "error");
  document
    .querySelectorAll("button")
    .forEach((button) => (button.disabled = true));
}
