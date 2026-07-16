let adminConfig = null;
const adminApp = document.getElementById('adminApp');
const adminPageTitle = document.getElementById('adminPageTitle');
const adminHeaderActions = document.getElementById('adminHeaderActions');

function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, function(char) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char];
  });
}

function isTruthy(value) {
  return ['1', 'true', 'yes', 'on'].includes(String(value == null ? '' : value).trim().toLowerCase());
}

function settingsGroupSlug() {
  var match = window.location.pathname.match(/^\/admin\/settings\/([a-z0-9-]+)$/);
  return match ? match[1] : '';
}

function setAdminHeader(title) {
  adminPageTitle.textContent = title;
  document.title = '牛牛1号';
  adminHeaderActions.innerHTML = "<a class='toplink' href='/'>返回首页</a>";
}

function renderAdminLogin(errorMessage) {
  setAdminHeader('设置验证');
  var message = errorMessage ? "<div class='error'>" + escapeHtml(errorMessage) + "</div>" : '';
  adminApp.innerHTML = "<form class='admin-login-box' data-admin-login-form>" +
    "<h2>设置页验证</h2>" +
    "<div class='admin-login-sub'>请输入管理员密码或本机管理员密钥后进入设置。</div>" +
    message +
    "<label for='adminCredential'>管理员凭据</label>" +
    "<input id='adminCredential' name='admin_password' type='password' autocomplete='current-password' required autofocus>" +
    "<button type='submit'>进入设置</button>" +
    "<div class='admin-login-hint'>凭据只会提交到当前 NiuOne 服务，不会保存在浏览器页面中。</div>" +
    "</form>";
  var input = document.getElementById('adminCredential');
  if (input) input.focus();
}

function renderSettingsIndex() {
  setAdminHeader('设置');
  var groups = Array.isArray(adminConfig.groups) ? adminConfig.groups : [];
  var cards = groups.map(function(group) {
    return "<a class='settings-card' href='/admin/settings/" + escapeHtml(group.slug) + "' data-settings-route " +
      "aria-label='进入" + escapeHtml(group.name) + "设置'>" +
      "<span class='settings-card-icon' aria-hidden='true'>" + escapeHtml(group.icon || '设置') + "</span>" +
      "<span class='settings-card-copy'>" +
      "<span class='settings-card-title'>" + escapeHtml(group.name) + "</span>" +
      "<span class='settings-card-summary'>" + escapeHtml(group.summary || '维护该分组的业务配置。') + "</span>" +
      "<span class='settings-card-meta'>" + Number(group.item_count || 0) + " 项设置</span>" +
      "</span><span class='settings-card-arrow' aria-hidden='true'>›</span></a>";
  }).join('');
  adminApp.innerHTML = "<div class='settings-index'>" +
    "<div class='settings-overview'><div class='settings-overview-copy'>" +
    "<h2>业务配置</h2>" +
    "</div><div class='settings-overview-stats'>" +
    "<div class='settings-stat'><span class='settings-stat-value'>" + groups.length + "</span><span class='settings-stat-label'>分组</span></div>" +
    "<div class='settings-stat'><span class='settings-stat-value'>" + (adminConfig.items || []).length + "</span><span class='settings-stat-label'>配置项</span></div>" +
    "</div></div><nav class='settings-grid' aria-label='设置分组'>" + cards + "</nav></div>";
}

function renderEnvInput(item) {
  var name = escapeHtml(item.name || '');
  var label = escapeHtml(item.label || item.name || '设置项');
  var fieldName = 'env__' + name;
  var value = String(item.file_value == null ? '' : item.file_value);
  var escapedValue = escapeHtml(value);
  var kind = String(item.kind || 'text');
  if (item.secret) {
    return "<input type='password' name='" + fieldName + "' aria-label='" + label + "' placeholder='" +
      escapeHtml(item.file_state || '未设置') + "' autocomplete='new-password'>";
  }
  if (kind === 'bool') {
    var current = isTruthy(value) ? '1' : (String(value).trim() === '' ? '' : '0');
    var noDefault = item.name === 'DASHBOARD_US_FEATURES_ENABLED' || item.bool_no_default;
    var toggle = item.name === 'DASHBOARD_US_FEATURES_ENABLED' ? " data-feature-toggle='us'" : '';
    return "<select name='" + fieldName + "'" + toggle + " aria-label='" + label + "'>" +
      (noDefault ? '' : "<option value=''" + (current === '' ? ' selected' : '') + ">默认</option>") +
      "<option value='1'" + (current === '1' ? ' selected' : '') + ">启用</option>" +
      "<option value='0'" + (current === '0' ? ' selected' : '') + ">停用</option></select>";
  }
  if (kind === 'api_mode') {
    var apiMode = ['auto', 'responses', 'chat'].indexOf(value) >= 0 ? value : 'auto';
    return "<select name='" + fieldName + "' aria-label='" + label + "'>" +
      "<option value='auto'" + (apiMode === 'auto' ? ' selected' : '') + ">自动</option>" +
      "<option value='responses'" + (apiMode === 'responses' ? ' selected' : '') + ">Responses API（搜索工具）</option>" +
      "<option value='chat'" + (apiMode === 'chat' ? ' selected' : '') + ">Chat Completions（兼容模式）</option></select>" +
      "<div class='config-meta'>自动模式下，Grok 4.5 使用 Responses API，其他模型保持 Chat Completions</div>";
  }
  if (kind === 'cron_time' || kind === 'time') {
    var dayLabel = kind === 'cron_time' && item.day_label ? ' · ' + escapeHtml(item.day_label) : '';
    return "<input type='time' name='" + fieldName + "' aria-label='" + label + "' value='" + escapedValue + "'>" +
      "<div class='config-meta'>北京时间" + dayLabel + "</div>";
  }
  if (kind === 'time_list' || kind === 'handle_list') {
    var values = kind === 'time_list' ? (item.time_values || []) : (item.handle_values || []);
    var inputType = kind === 'time_list' ? 'time' : 'text';
    var placeholder = kind === 'handle_list' ? 'handle' : '';
    var rows = values.map(function(entry, index) {
      return "<div class='time-list-item'><input type='" + inputType + "' name='" + fieldName + "' " +
        "aria-label='" + label + ' ' + (index + 1) + "' value='" + escapeHtml(entry) + "'" +
        (placeholder ? " placeholder='" + placeholder + "' autocapitalize='off' spellcheck='false'" : '') + ">" +
        "<button type='button' class='time-list-remove' data-time-list-remove aria-label='删除" +
        (kind === 'time_list' ? '时间点' : '作者') + "'>x</button></div>";
    }).join('');
    return "<div class='time-list-control' data-time-list data-field-name='" + fieldName + "' data-input-type='" + inputType +
      "' data-placeholder='" + placeholder + "' data-input-label='" + label + "'>" +
      "<input type='hidden' name='" + fieldName + "' value=''><div class='time-list-grid' data-time-list-items>" + rows +
      "</div><button type='button' class='time-list-add' data-time-list-add aria-label='添加" +
      (kind === 'time_list' ? '时间点' : '作者') + "'>+</button></div>" +
      "<div class='config-meta'>" + (kind === 'time_list' ? '北京时间' : 'X/Twitter handle') + "</div>";
  }
  if (kind === 'stock_universe') {
    var universeSelected = new Set(item.stock_universe_values || []);
    return "<div class='strategy-multi-control'><input type='hidden' name='" + fieldName + "' value=''>" +
      (item.stock_universe_options || []).map(function(option) {
        var id = escapeHtml(option.id || '');
        return "<label class='strategy-option' style='--strategy-color:" + escapeHtml(option.color || '#94a3b8') + "'>" +
          "<input type='checkbox' name='" + fieldName + "' value='" + id + "'" +
          (universeSelected.has(option.id) ? ' checked' : '') + " aria-label='" + label + '：' + escapeHtml(option.label || option.id) + "'>" +
          "<span class='strategy-option-main'><span class='strategy-option-title'><span class='strategy-option-dot'></span>" +
          escapeHtml(option.label || option.id) + "</span><span class='strategy-option-desc'>" + escapeHtml(option.desc || '') +
          "</span></span></label>";
      }).join('') + "</div><div class='config-meta'>至少选择一项；ST 为跨板块独立范围，卖出已有持仓不受此设置限制</div>";
  }
  if (kind === 'strategy_source' || kind === 'strategy_suite') {
    var strategyOptions = kind === 'strategy_suite' ? (item.strategy_suite_options || []) : (item.strategy_source_options || []);
    return "<div class='strategy-multi-control'>" + strategyOptions.map(function(option) {
      var id = escapeHtml(option.id || '');
      return "<label class='strategy-option' style='--strategy-color:" + escapeHtml(option.color || '#94a3b8') + "'>" +
        "<input type='radio' name='" + fieldName + "' value='" + id + "'" + (value === option.id ? ' checked' : '') +
        " aria-label='" + label + '：' + escapeHtml(option.label || option.id) + "' data-strategy-source-toggle>" +
        "<span class='strategy-option-main'><span class='strategy-option-title'><span class='strategy-option-dot'></span>" +
        escapeHtml(option.label || option.id) + "</span><span class='strategy-option-desc'>" + escapeHtml(option.desc || '') +
        "</span></span></label>";
    }).join('') + "</div><div class='config-meta'>每轮只启用一套完整策略；候选、买入、卖出和仓位规则互不混用</div>";
  }
  if (kind === 'preset_strategy_text' || kind === 'trade_discipline_text') {
    var preset = kind === 'preset_strategy_text';
    var maxChars = Number(preset ? item.preset_strategy_max_chars : item.trade_discipline_max_chars) || 4000;
    return "<textarea class='" + (preset ? 'preset-strategy-textarea' : 'trade-discipline-textarea') + "' name='" +
      fieldName + "' aria-label='" + label + "' maxlength='" + maxChars + "' spellcheck='false' placeholder='" +
      (preset ? '例如：只做主线强趋势回踩，买入后跌破5日线离场。' : '留空时使用内置交易纪律') + "'>" +
      escapedValue + "</textarea><div class='config-meta'>" +
      (preset ? '激活后由买卖决策模型优化为选股、买入、卖出和仓位规则' : '直接写入买卖决策模型 prompt 的“必须遵守”段') + "</div>";
  }
  if (kind === 'strategy_multi' || kind === 'strategy_single') {
    var selected = new Set(item.strategy_values || []);
    var inputType = kind === 'strategy_single' ? 'radio' : 'checkbox';
    return "<div class='strategy-multi-control'><input type='hidden' name='" + fieldName + "' value=''>" +
      (item.strategy_options || []).map(function(option) {
        var id = escapeHtml(option.id || '');
        return "<label class='strategy-option' style='--strategy-color:" + escapeHtml(option.color || '#94a3b8') + "'>" +
          "<input type='" + inputType + "' name='" + fieldName + "' value='" + id + "'" +
          (selected.has(option.id) ? ' checked' : '') + " aria-label='" + label + '：' + escapeHtml(option.label || option.id) + "'>" +
          "<span class='strategy-option-main'><span class='strategy-option-title'><span class='strategy-option-dot'></span>" +
          escapeHtml(option.label || option.id) + "</span><span class='strategy-option-desc'>" + escapeHtml(option.desc || '') +
          "</span></span></label>";
      }).join('') + "</div><div class='config-meta'>每次只启用一个内置策略</div>";
  }
  if (kind === 'context_length' || kind === 'max_tokens') {
    var context = kind === 'context_length';
    return "<input type='text' name='" + fieldName + "' aria-label='" + label + "' value='" + escapedValue +
      "' placeholder='默认 " + (context ? '128000；例如 128K、1M 或 1000000' : '4096；例如 2048 或 8192') +
      "' inputmode='numeric'><div class='config-meta'>默认 " + (context ? '128000 tokens；填写后保存为数字 tokens' : '4096 tokens；按所选接口映射为兼容的输出长度参数') + "</div>";
  }
  return "<input type='" + (kind === 'int' ? 'number' : 'text') + "' name='" + fieldName +
    "' aria-label='" + label + "' value='" + escapedValue + "'>";
}

function renderNotificationField(item, compact) {
  var current = item.current_state ? escapeHtml(item.current_state) : "<span class='config-empty'>未设置</span>";
  return "<div class='" + (compact ? 'notification-compact-field' : 'notification-field') + "' data-notification-field='" +
    escapeHtml(item.name) + "'><div class='notification-field-label'>" + escapeHtml(item.label || item.name) +
    "</div><div>" + renderEnvInput(item) + "</div><div class='config-meta'>当前状态：<span data-env-current='" +
    escapeHtml(item.name) + "'>" + current + "</span></div></div>";
}

function renderNotificationSettings(items) {
  var byName = Object.fromEntries(items.map(function(item) { return [item.name, item]; }));
  var general = (adminConfig.notification_general_names || []).filter(function(name) { return byName[name]; }).map(function(name) {
    return renderNotificationField(byName[name], true);
  }).join('');
  var selectedCount = 0;
  var options = ["<option value=''>选择通知渠道</option>"];
  var cards = (adminConfig.notification_channels || []).map(function(channel) {
    var enabled = byName[channel.enabled_name] || {};
    var active = isTruthy(enabled.effective || enabled.file_value || '0');
    var configured = active || String(enabled.file_value || '').trim() !== '' || (channel.field_names || []).some(function(name) {
      var item = byName[name] || {};
      var state = String(item.current_state || '').trim();
      return String(item.file_value || '').trim() !== '' || (state !== '' && state !== '未设置');
    });
    if (configured) selectedCount += 1;
    options.push("<option value='" + escapeHtml(channel.id) + "'" + (configured ? ' hidden disabled' : '') + '>' + escapeHtml(channel.label) + '</option>');
    var fields = (channel.field_names || []).filter(function(name) { return byName[name]; }).map(function(name) {
      return renderNotificationField(byName[name], false);
    }).join('');
    return "<article class='notification-channel-card' data-notification-channel-card='" + escapeHtml(channel.id) + "'" +
      " data-notification-channel-added='" + (configured ? '1' : '0') + "' data-notification-channel-active='" + (active ? 'true' : 'false') + "'" +
      (configured ? '' : ' hidden') + " aria-hidden='" + (configured ? 'false' : 'true') + "'>" +
      "<input type='hidden' name='env__" + escapeHtml(channel.enabled_name) + "' value='" + (active ? '1' : '0') + "' data-notification-channel-enabled>" +
      "<input type='hidden' name='notification_remove__" + escapeHtml(channel.id) + "' value='0' data-notification-channel-removed>" +
      "<div class='notification-channel-card-head'><div><div class='notification-channel-name' id='notification-channel-name-" + escapeHtml(channel.id) + "'>" +
      escapeHtml(channel.label) + "</div><div class='notification-channel-desc'>" + escapeHtml(channel.description || '') +
      "</div></div><div class='notification-channel-head-actions'><div class='notification-channel-control'>" +
      "<button type='button' class='notification-channel-activation" + (active ? ' is-active' : '') + "' data-notification-channel-activation role='switch'" +
      " aria-checked='" + (active ? 'true' : 'false') + "' aria-label='" + escapeHtml(channel.label) + "渠道通知'>" +
      "<span class='notification-channel-switch-track' aria-hidden='true'><span class='notification-channel-switch-thumb'></span></span>" +
      "<span class='notification-channel-activation-state' data-notification-channel-activation-state>" + (active ? '已启用' : '已关闭') + "</span></button></div>" +
      "<button type='button' class='notification-channel-remove' data-notification-channel-remove='" + escapeHtml(channel.id) + "'>移除</button></div></div>" +
      "<fieldset class='notification-channel-fields' data-notification-channel-fields" + (configured ? '' : ' disabled') +
      " aria-labelledby='notification-channel-name-" + escapeHtml(channel.id) + "'>" + fields + "</fieldset>" +
      "<div class='notification-channel-actions'><button type='button' class='notification-channel-test' data-notification-channel-test='" +
      escapeHtml(channel.id) + "' aria-describedby='notification-test-status-" + escapeHtml(channel.id) + "'>发送测试通知</button>" +
      "<div class='notification-channel-test-copy'><span class='notification-channel-test-note'>测试通知不受渠道开关影响</span>" +
      "<span class='notification-channel-test-status' id='notification-test-status-" + escapeHtml(channel.id) +
      "' data-notification-channel-test-status role='status' aria-live='polite'></span></div></div></article>";
  }).join('');
  return "<div class='notification-settings' data-notification-channels><div class='notification-block'>" +
    "<div class='notification-block-head'><div><div class='notification-block-title'>基础设置</div>" +
    "<div class='notification-block-note'>总开关用于临时关闭全部渠道，不会删除任何渠道配置。</div></div></div>" +
    "<div class='notification-general-grid'>" + general + "</div></div><div class='notification-block'>" +
    "<div class='notification-block-head'><div><div class='notification-block-title'>通知渠道</div>" +
    "<div class='notification-block-note'>每个渠道可单独启用或关闭；关闭会保留配置，移除并保存后才会清除配置。</div></div></div>" +
    "<div class='notification-channel-add-row'><select data-notification-channel-picker aria-label='选择通知渠道'>" + options.join('') +
    "</select><button type='button' class='notification-channel-add' data-notification-channel-add disabled>添加渠道</button></div>" +
    "<div class='notification-channel-empty' data-notification-channel-empty" + (selectedCount ? ' hidden' : '') + ">尚未添加通知渠道</div>" +
    "<div class='notification-channel-grid' data-notification-channel-list>" + cards + "</div></div></div>";
}

function renderSettingsGroup(slug) {
  var group = (adminConfig.groups || []).find(function(entry) { return entry.slug === slug; });
  if (!group) {
    setAdminHeader('设置分组不存在');
    adminApp.innerHTML = "<div class='errmsg'>未找到该设置分组。<a class='toplink' href='/admin' data-settings-route>返回全部设置</a></div>";
    return;
  }
  setAdminHeader(group.name);
  var items = (adminConfig.items || []).filter(function(item) { return String(item.group || '其他') === group.name; });
  var toggleName = adminConfig.ui && adminConfig.ui.us_feature_toggle_name;
  var toggle = (adminConfig.items || []).find(function(item) { return item.name === toggleName; });
  var usEnabled = !!toggle && isTruthy(toggle.effective || toggle.file_value);
  var gatedNames = new Set((adminConfig.ui && adminConfig.ui.us_feature_gated_names) || []);
  var strategyPreset = adminConfig.ui && adminConfig.ui.strategy_preset_name;
  var body;
  var countLabel;
  if (group.name === '交易通知') {
    body = renderNotificationSettings(items);
    countLabel = (adminConfig.notification_channels || []).length + ' 个渠道';
  } else {
    body = "<div class='settings-list'>" + items.map(function(item) {
      var attrs = '';
      if (gatedNames.has(item.name)) attrs += " data-feature-gated='us'" + (usEnabled ? " aria-hidden='false'" : " hidden aria-hidden='true'");
      if (item.name === strategyPreset) attrs += " data-strategy-source-gated='preset_text'";
      var current = item.current_state ? escapeHtml(item.current_state) : "<span class='config-empty'>未设置</span>";
      var defaultValue = item.default ? escapeHtml(item.default) : "<span class='config-empty'>未设置</span>";
      return "<div class='setting-row'" + attrs + "><div class='setting-copy'><div class='config-label'>" +
        escapeHtml(item.label || item.name) + "</div></div><div class='setting-editor'>" + renderEnvInput(item) +
        "</div><div class='setting-state'><div class='setting-state-item'><div class='setting-state-label'>当前状态</div>" +
        "<div class='config-meta' data-env-current='" + escapeHtml(item.name) + "'>" + current + "</div></div>" +
        "<div class='setting-state-item'><div class='setting-state-label'>默认</div><div class='config-meta'>" + defaultValue +
        "</div></div></div></div>";
    }).join('') + '</div>';
    countLabel = items.length + ' 项';
  }
  adminApp.innerHTML = "<div class='settings-detail'><nav class='settings-breadcrumbs' aria-label='设置导航'>" +
    "<a class='settings-back-link' href='/admin' data-settings-route><span aria-hidden='true'>←</span><span>全部设置</span></a></nav>" +
    "<form id='env-config-form' class='settings-form' data-settings-group='" + escapeHtml(slug) +
    "' data-save-endpoint='/api/admin/config/env/" + escapeHtml(slug) + "'>" +
    "<input type='hidden' name='settings_group' value='" + escapeHtml(slug) + "'>" +
    "<section class='settings-group' id='settings-" + escapeHtml(slug) + "'><div class='settings-group-head'><div><h2>" +
    escapeHtml(group.name) + "</h2>" + (group.note ? "<p class='settings-group-note'>" + escapeHtml(group.note) + "</p>" : '') +
    "</div><span class='settings-count'>" + escapeHtml(countLabel) + "</span></div>" + body +
    "<div class='settings-actions'><div class='settings-save-status' data-env-save-status role='status' aria-live='polite'></div>" +
    "<button class='save-button' data-env-save-button type='submit'>保存本组设置</button></div></section></form></div>";
  syncUsFeatureSettings();
  syncStrategySourceSettings();
  syncNotificationChannelSettings();
  initializeEnvForm();
}

function renderAdminRoute() {
  if (!adminConfig) return;
  var slug = settingsGroupSlug();
  if (slug) renderSettingsGroup(slug);
  else renderSettingsIndex();
}

async function loadAdminConfig() {
  var response = await fetch('/api/admin/config', {credentials: 'same-origin', cache: 'no-store'});
  if (response.status === 403) {
    adminConfig = null;
    renderAdminLogin('');
    return;
  }
  var payload = await response.json().catch(function() { return null; });
  if (!response.ok || !payload || !Array.isArray(payload.items)) throw new Error((payload && payload.error) || '设置加载失败');
  adminConfig = payload;
  renderAdminRoute();
}

document.addEventListener('submit', function(event) {
  var form = event.target;
  if (!form || !form.matches('[data-admin-login-form]')) return;
  event.preventDefault();
  var button = form.querySelector('button[type="submit"]');
  if (button) { button.disabled = true; button.textContent = '验证中...'; }
  fetch('/api/admin/session', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
    body: new URLSearchParams(new FormData(form)),
  }).then(function(response) {
    return response.json().catch(function() { return null; }).then(function(payload) {
      if (!response.ok || !payload || payload.ok !== true) throw new Error((payload && payload.error) || '管理员凭据错误');
      return loadAdminConfig();
    });
  }).catch(function(error) {
    renderAdminLogin(error && error.message ? error.message : '管理员凭据错误');
  });
});

document.addEventListener('click', function(event) {
  var link = event.target && event.target.closest ? event.target.closest('[data-settings-route]') : null;
  if (!link) return;
  var form = document.getElementById('env-config-form');
  if (form && form.dataset.savedState === '0' && !window.confirm('当前分组有未保存修改，确定离开吗？')) {
    event.preventDefault();
    return;
  }
  event.preventDefault();
  window.history.pushState({}, '', link.getAttribute('href'));
  renderAdminRoute();
  window.scrollTo(0, 0);
});

window.addEventListener('popstate', renderAdminRoute);

loadAdminConfig().catch(function(error) {
  setAdminHeader('设置加载失败');
  adminApp.innerHTML = "<div class='errmsg'>" + escapeHtml(error && error.message ? error.message : '设置加载失败') + "</div>";
});

function syncUsFeatureSettings() {
  var toggle = document.querySelector('[data-feature-toggle="us"]');
  var enabled = toggle && toggle.value === '1';
  document.querySelectorAll('[data-feature-gated="us"]').forEach(function(section) {
    section.hidden = !enabled;
    section.setAttribute('aria-hidden', enabled ? 'false' : 'true');
  });
}
function currentStrategySource() {
  var checked = document.querySelector('[data-strategy-source-toggle]:checked');
  return checked ? checked.value : 'zettaranc';
}
function syncStrategySourceSettings() {
  var source = currentStrategySource();
  document.querySelectorAll('[data-strategy-source-gated]').forEach(function(section) {
    var enabled = section.getAttribute('data-strategy-source-gated') === source;
    section.hidden = !enabled;
    section.setAttribute('aria-hidden', enabled ? 'false' : 'true');
  });
}
function setNotificationChannelVisibility(card, active) {
  if (!card) return;
  var enabledInput = card.querySelector('[data-notification-channel-enabled]');
  var fields = card.querySelector('[data-notification-channel-fields]');
  card.setAttribute('data-notification-channel-added', active ? '1' : '0');
  card.hidden = !active;
  card.setAttribute('aria-hidden', active ? 'false' : 'true');
  if (enabledInput) enabledInput.disabled = !active;
  if (fields) fields.disabled = !active;
}
function setNotificationChannelActivation(card, active) {
  if (!card) return;
  var enabledInput = card.querySelector('[data-notification-channel-enabled]');
  var button = card.querySelector('[data-notification-channel-activation]');
  var state = card.querySelector('[data-notification-channel-activation-state]');
  if (enabledInput) enabledInput.value = active ? '1' : '0';
  card.setAttribute('data-notification-channel-active', active ? 'true' : 'false');
  if (button) {
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-checked', active ? 'true' : 'false');
  }
  if (state) state.textContent = active ? '已启用' : '已关闭';
}
function setNotificationChannelRemoved(card, removed) {
  if (!card) return;
  var removedInput = card.querySelector('[data-notification-channel-removed]');
  if (removedInput) removedInput.value = removed ? '1' : '0';
}
function syncNotificationChannelSettings() {
  var root = document.querySelector('[data-notification-channels]');
  if (!root) return;
  var picker = root.querySelector('[data-notification-channel-picker]');
  var addButton = root.querySelector('[data-notification-channel-add]');
  var empty = root.querySelector('[data-notification-channel-empty]');
  var activeCount = 0;
  root.querySelectorAll('[data-notification-channel-card]').forEach(function(card) {
    var enabledInput = card.querySelector('[data-notification-channel-enabled]');
    var active = !!enabledInput && enabledInput.value === '1';
    var added = card.getAttribute('data-notification-channel-added') === '1';
    var channelId = card.getAttribute('data-notification-channel-card') || '';
    setNotificationChannelVisibility(card, added);
    setNotificationChannelActivation(card, active);
    if (added) activeCount += 1;
    if (picker) {
      Array.prototype.forEach.call(picker.options, function(option) {
        if (option.value !== channelId) return;
        option.hidden = added;
        option.disabled = added;
      });
    }
  });
  if (picker && picker.selectedOptions.length && picker.selectedOptions[0].disabled) picker.value = '';
  if (addButton) addButton.disabled = !picker || !picker.value;
  if (empty) empty.hidden = activeCount > 0;
}
document.addEventListener('DOMContentLoaded', syncUsFeatureSettings);
document.addEventListener('DOMContentLoaded', syncStrategySourceSettings);
document.addEventListener('DOMContentLoaded', syncNotificationChannelSettings);
syncUsFeatureSettings();
syncStrategySourceSettings();
syncNotificationChannelSettings();
function handleUsFeatureToggle(event) {
  var target = event.target;
  if (target && target.matches && target.matches('[data-feature-toggle="us"]')) {
    syncUsFeatureSettings();
  }
}
function handleStrategySourceToggle(event) {
  var target = event.target;
  if (target && target.matches && target.matches('[data-strategy-source-toggle]')) {
    syncStrategySourceSettings();
  }
}
function handleNotificationChannelPicker(event) {
  var target = event.target;
  if (target && target.matches && target.matches('[data-notification-channel-picker]')) {
    syncNotificationChannelSettings();
  }
}
document.addEventListener('input', handleUsFeatureToggle);
document.addEventListener('change', handleUsFeatureToggle);
document.addEventListener('input', handleStrategySourceToggle);
document.addEventListener('change', handleStrategySourceToggle);
document.addEventListener('change', handleNotificationChannelPicker);
function pulseSaveButton(button) {
  if (!button) return;
  button.classList.add('pressed');
  window.setTimeout(function() { button.classList.remove('pressed'); }, 180);
}
document.addEventListener('pointerdown', function(event) {
  var target = event.target;
  if (!target || !target.closest) return;
  var button = target.closest('[data-env-save-button]');
  if (button && !button.disabled) pulseSaveButton(button);
});
function envFormSnapshot(form) {
  if (!form || !window.FormData || !window.URLSearchParams) return '';
  var data = new FormData(form);
  form.querySelectorAll('input[type="password"][name]').forEach(function(input) {
    data.set(input.name, '');
  });
  return new URLSearchParams(data).toString();
}
function envFormHasUnsavedSecret(form) {
  if (!form) return false;
  return Array.prototype.some.call(
    form.querySelectorAll('input[type="password"]'),
    function(input) { return input.value !== ''; }
  );
}
var envSavedSnapshots = new WeakMap();
var envSaveResultTimers = new WeakMap();
function clearEnvSaveResult(form) {
  if (!form) return;
  var timer = envSaveResultTimers.get(form);
  if (timer) window.clearTimeout(timer);
  envSaveResultTimers.delete(form);
  delete form.dataset.saveResult;
}
function brieflyShowEnvSaved(form) {
  if (!form) return;
  clearEnvSaveResult(form);
  form.dataset.saveResult = 'ok';
  var timer = window.setTimeout(function() {
    envSaveResultTimers.delete(form);
    if (form.dataset.savedState === '1' && form.dataset.saveResult === 'ok') delete form.dataset.saveResult;
  }, 1600);
  envSaveResultTimers.set(form, timer);
}
function markEnvFormSaved(form) {
  if (!form) return;
  envSavedSnapshots.set(form, envFormSnapshot(form));
  form.dataset.savedState = '1';
  var button = form.querySelector('[data-env-save-button]');
  if (button) {
    if (!button.dataset.defaultText) button.dataset.defaultText = button.textContent || '保存本组设置';
    button.disabled = true;
    button.classList.add('saved');
    button.textContent = '已保存';
  }
}
function resetEnvSaveIfDirty(form) {
  if (!form || form.id !== 'env-config-form' || !envSavedSnapshots.has(form)) return;
  clearEnvSaveResult(form);
  form.dataset.editRevision = String(Number(form.dataset.editRevision || '0') + 1);
  var savedSnapshot = envSavedSnapshots.get(form);
  var currentSnapshot = envFormSnapshot(form);
  if (!currentSnapshot) return;
  if (currentSnapshot === savedSnapshot && !envFormHasUnsavedSecret(form)) {
    if (form.dataset.savedState === '0') {
      markEnvFormSaved(form);
      var status = form.querySelector('[data-env-save-status]');
      if (status) {
        status.textContent = '';
        status.className = 'settings-save-status';
      }
    }
    return;
  }
  if (form.dataset.savedState === '0') return;
  form.dataset.savedState = '0';
  setEnvSaveFeedback(form, '', '有未保存修改');
}
function setEnvSaveFeedback(form, state, message) {
  var button = form ? form.querySelector('[data-env-save-button]') : null;
  var status = form ? form.querySelector('[data-env-save-status]') : null;
  if (status) {
    status.textContent = message || '';
    status.className = 'settings-save-status' + (state ? ' ' + state : '');
  }
  if (!button) return;
  if (!button.dataset.defaultText) button.dataset.defaultText = button.textContent || '保存本组设置';
  button.classList.remove('saved', 'error');
  if (state === 'busy') {
    button.disabled = true;
    button.textContent = '保存中...';
  } else if (state === 'ok') {
    button.disabled = false;
    button.classList.add('saved');
    button.textContent = '已保存';
    markEnvFormSaved(form);
    brieflyShowEnvSaved(form);
  } else if (state === 'error') {
    button.disabled = false;
    button.classList.add('error');
    button.textContent = '保存失败';
  } else {
    button.disabled = false;
    button.textContent = button.dataset.defaultText || '保存本组设置';
  }
}
function initializeEnvForm() {
  var form = document.getElementById('env-config-form');
  if (form) {
    form.dataset.editRevision = '0';
    markEnvFormSaved(form);
  }
}
document.addEventListener('DOMContentLoaded', initializeEnvForm);
initializeEnvForm();
document.addEventListener('input', function(event) {
  var target = event.target;
  var form = target && target.closest ? target.closest('#env-config-form') : null;
  resetEnvSaveIfDirty(form);
});
document.addEventListener('change', function(event) {
  var target = event.target;
  var form = target && target.closest ? target.closest('#env-config-form') : null;
  resetEnvSaveIfDirty(form);
});
document.addEventListener('keydown', function(event) {
  if (!event || String(event.key || '').toLowerCase() !== 's' || (!event.metaKey && !event.ctrlKey) || event.altKey) return;
  var form = document.getElementById('env-config-form');
  var button = form ? form.querySelector('[data-env-save-button]') : null;
  if (!form || form.dataset.savedState !== '0' || !button || button.disabled) return;
  event.preventDefault();
  if (form.requestSubmit) form.requestSubmit(button);
  else button.click();
});
function businessSaveMessage(payload) {
  if (!payload || payload.ok === false) return '保存失败';
  if (!payload.changed) return '配置未变化，无需重新应用';
  var count = Number(payload.changed_count || 0);
  var applied = ((payload.runtime && payload.runtime.applied) || []).filter(function(item) { return item !== 'env'; });
  var message = '已保存 ' + count + ' 项';
  if (applied.length) message += '，已热应用：' + applied.join('、');
  return message;
}
function applyEnvConfigState(form, config) {
  if (!form || !config || !Array.isArray(config.items)) return;
  var currentNodes = Object.create(null);
  var secretInputs = Object.create(null);
  form.querySelectorAll('[data-env-current]').forEach(function(node) {
    var name = node.getAttribute('data-env-current') || '';
    if (name) currentNodes[name] = node;
  });
  form.querySelectorAll('input[type="password"][name^="env__"]').forEach(function(input) {
    secretInputs[input.name.slice(5)] = input;
  });
  config.items.forEach(function(item) {
    var name = String((item && item.name) || '');
    if (!name) return;
    var state = String((item && item.current_state) || '');
    var currentNode = currentNodes[name];
    if (currentNode) {
      currentNode.textContent = state || '未设置';
      currentNode.classList.toggle('config-empty', !state);
    }
    var secretInput = secretInputs[name];
    if (secretInput && item.secret === true) {
      secretInput.value = '';
      secretInput.placeholder = String(item.file_state || '未设置');
    }
  });
}
function clearRemovedNotificationChannelFields(form) {
  if (!form) return;
  form.querySelectorAll('[data-notification-channel-card]').forEach(function(card) {
    var removedInput = card.querySelector('[data-notification-channel-removed]');
    if (!removedInput || removedInput.value !== '1') return;
    card.querySelectorAll('[data-notification-channel-fields] input, [data-notification-channel-fields] select, [data-notification-channel-fields] textarea').forEach(function(field) {
      field.value = '';
    });
    setNotificationTestFeedback(
      card.querySelector('[data-notification-channel-test]'),
      '',
      ''
    );
  });
}
function setNotificationTestFeedback(button, state, message) {
  if (!button) return;
  var card = button.closest('[data-notification-channel-card]');
  var status = card ? card.querySelector('[data-notification-channel-test-status]') : null;
  if (!button.dataset.defaultText) button.dataset.defaultText = button.textContent || '发送测试通知';
  button.classList.remove('is-busy', 'is-ok', 'is-error');
  button.disabled = state === 'busy';
  if (state) button.classList.add('is-' + state);
  button.textContent = state === 'busy' ? '发送中...' : button.dataset.defaultText;
  if (status) {
    status.textContent = message || '';
    status.className = 'notification-channel-test-status' + (state ? ' is-' + state : '');
  }
}
function notificationTestBody(card) {
  var params = new URLSearchParams();
  var channelId = card ? card.getAttribute('data-notification-channel-card') : '';
  params.set('channel', channelId || '');
  if (!card) return params;
  card.querySelectorAll('[data-notification-channel-fields] [name^="env__"]').forEach(function(input) {
    params.set(input.name, String(input.value || '').trim());
  });
  var form = card.closest('form');
  var timeout = form ? form.querySelector('[name="env__DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS"]') : null;
  if (timeout) params.set(timeout.name, String(timeout.value || '').trim());
  return params;
}
document.addEventListener('submit', function(event) {
  var form = event.target;
  if (!form || form.id !== 'env-config-form') return;
  if (!window.fetch || !window.FormData || !window.URLSearchParams) return;
  event.preventDefault();
  var requestBody = new URLSearchParams(new FormData(form));
  var submittedSafeSnapshot = envFormSnapshot(form);
  var submittedRevision = form.dataset.editRevision || '0';
  var saveEndpoint = form.getAttribute('data-save-endpoint') || '/api/admin/config/env';
  setEnvSaveFeedback(form, 'busy', '正在保存本组设置...');
  fetch(saveEndpoint, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'Accept': 'application/json', 'X-NiuOne-Action': '1'},
    body: requestBody
  }).then(function(response) {
    return response.json().catch(function() { return null; }).then(function(payload) {
      if (!response.ok || !payload || payload.ok === false) {
        throw new Error((payload && payload.error) || '保存失败，请确认登录状态后重试');
      }
      return payload;
    });
  }).then(function(payload) {
    if (payload.config && Array.isArray(payload.config.items)) adminConfig = payload.config;
    var formUnchanged = (form.dataset.editRevision || '0') === submittedRevision;
    if (formUnchanged) {
      applyEnvConfigState(form, payload.config);
      clearRemovedNotificationChannelFields(form);
    }
    if (payload.reauth_required) {
      markEnvFormSaved(form);
      window.location.replace('/admin');
      return;
    }
    syncUsFeatureSettings();
    syncStrategySourceSettings();
    syncNotificationChannelSettings();
    if (formUnchanged) {
      setEnvSaveFeedback(form, 'ok', businessSaveMessage(payload));
    } else {
      envSavedSnapshots.set(form, submittedSafeSnapshot);
      form.dataset.savedState = '0';
      setEnvSaveFeedback(form, '', businessSaveMessage(payload) + '；保存期间有新的修改，请再次保存');
    }
  }).catch(function(error) {
    setEnvSaveFeedback(form, 'error', error && error.message ? error.message : '保存失败，请稍后重试');
  });
});
window.addEventListener('beforeunload', function(event) {
  var form = document.getElementById('env-config-form');
  if (!form || form.dataset.savedState !== '0') return;
  event.preventDefault();
  event.returnValue = '';
});
document.addEventListener('click', function(event) {
  var target = event.target;
  if (!target || !target.closest) return;
  var notificationActivationButton = target.closest('[data-notification-channel-activation]');
  if (notificationActivationButton) {
    var activationCard = notificationActivationButton.closest('[data-notification-channel-card]');
    var activationInput = activationCard ? activationCard.querySelector('[data-notification-channel-enabled]') : null;
    setNotificationChannelActivation(activationCard, !activationInput || activationInput.value !== '1');
    resetEnvSaveIfDirty(notificationActivationButton.closest('form'));
    return;
  }
  var notificationTestButton = target.closest('[data-notification-channel-test]');
  if (notificationTestButton) {
    event.preventDefault();
    if (!window.fetch || !window.URLSearchParams) {
      setNotificationTestFeedback(notificationTestButton, 'error', '当前浏览器不支持在线测试');
      return;
    }
    var testCard = notificationTestButton.closest('[data-notification-channel-card]');
    setNotificationTestFeedback(notificationTestButton, 'busy', '正在验证并发送...');
    fetch('/api/admin/notifications/test', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'Accept': 'application/json', 'X-NiuOne-Action': '1'},
      body: notificationTestBody(testCard)
    }).then(function(response) {
      return response.json().catch(function() { return null; }).then(function(payload) {
        if (!response.ok || !payload || payload.ok !== true) {
          throw new Error((payload && payload.error) || '测试通知发送失败，请确认配置后重试');
        }
        return payload;
      });
    }).then(function(payload) {
      setNotificationTestFeedback(notificationTestButton, 'ok', payload.message || '测试通知已发送');
    }).catch(function(error) {
      setNotificationTestFeedback(notificationTestButton, 'error', error && error.message ? error.message : '测试通知发送失败');
    });
    return;
  }
  var notificationAddButton = target.closest('[data-notification-channel-add]');
  if (notificationAddButton) {
    var notificationRoot = notificationAddButton.closest('[data-notification-channels]');
    var picker = notificationRoot ? notificationRoot.querySelector('[data-notification-channel-picker]') : null;
    var channelId = picker ? picker.value : '';
    var card = null;
    if (notificationRoot && channelId) {
      notificationRoot.querySelectorAll('[data-notification-channel-card]').forEach(function(candidate) {
        if (candidate.getAttribute('data-notification-channel-card') === channelId) card = candidate;
      });
    }
    if (!card) return;
    setNotificationChannelVisibility(card, true);
    setNotificationChannelActivation(card, true);
    setNotificationChannelRemoved(card, false);
    if (picker) picker.value = '';
    syncNotificationChannelSettings();
    resetEnvSaveIfDirty(notificationAddButton.closest('form'));
    var firstInput = card.querySelector('[data-notification-channel-fields] input, [data-notification-channel-fields] select');
    if (firstInput) firstInput.focus();
    return;
  }
  var notificationRemoveButton = target.closest('[data-notification-channel-remove]');
  if (notificationRemoveButton) {
    var notificationCard = notificationRemoveButton.closest('[data-notification-channel-card]');
    var notificationForm = notificationRemoveButton.closest('form');
    setNotificationChannelVisibility(notificationCard, false);
    setNotificationChannelActivation(notificationCard, false);
    setNotificationChannelRemoved(notificationCard, true);
    syncNotificationChannelSettings();
    resetEnvSaveIfDirty(notificationForm);
    var notificationPicker = notificationForm ? notificationForm.querySelector('[data-notification-channel-picker]') : null;
    if (notificationPicker) notificationPicker.focus();
    return;
  }
  var addButton = target.closest('[data-time-list-add]');
  if (addButton) {
    var control = addButton.closest('[data-time-list]');
    var items = control ? control.querySelector('[data-time-list-items]') : null;
    var fieldName = control ? control.getAttribute('data-field-name') : '';
    if (!items || !fieldName) return;
    var item = document.createElement('div');
    item.className = 'time-list-item';
    var input = document.createElement('input');
    input.type = control.getAttribute('data-input-type') || 'time';
    input.name = fieldName;
    input.placeholder = control.getAttribute('data-placeholder') || '';
    var inputLabel = control.getAttribute('data-input-label') || '';
    if (inputLabel) {
      input.setAttribute('aria-label', inputLabel + ' ' + (items.children.length + 1));
    }
    if (input.type === 'text') {
      input.autocapitalize = 'off';
      input.spellcheck = false;
    }
    var removeButton = document.createElement('button');
    removeButton.type = 'button';
    removeButton.className = 'time-list-remove';
    removeButton.setAttribute('data-time-list-remove', '');
    removeButton.setAttribute('aria-label', '删除时间点');
    removeButton.title = '删除时间点';
    removeButton.textContent = 'x';
    item.appendChild(input);
    item.appendChild(removeButton);
    items.appendChild(item);
    resetEnvSaveIfDirty(control.closest('form'));
    input.focus();
    return;
  }
  var removeButton = target.closest('[data-time-list-remove]');
  if (removeButton) {
    var item = removeButton.closest('.time-list-item');
    var form = removeButton.closest('form');
    if (item) item.remove();
    resetEnvSaveIfDirty(form);
  }
});
