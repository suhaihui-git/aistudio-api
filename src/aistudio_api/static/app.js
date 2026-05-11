
function app() {
  return {
    view: 'chat', sidebarOpen: false, configOpen: false, openSelect: null,
    authed: false, authChecked: false, login: { password: '', busy: false, error: '' },
    stats: {}, rotationMode: 'round_robin', rotCfg: { mode: 'round_robin', cooldown: 60 },
    apiKeys: [], newKeyName: '默认 Key', createdKey: '', apiKeyInput: '',
    accounts: [], rotationAccounts: {}, activeId: '', activeAccount: {},
    models: [], model: '',
    msgs: [], draft: '', selectedImages: [], busy: false,
    cfg: { thinking: 'off', search: 'off', stream: 'on', temperature: 1.0, topP: 0.95, maxTokens: 32768, safety: 'on' },
    toast: { show: false, msg: '', t: null },
    cookieModal: { open: false, cookies: '', name: '', email: '', importing: false },
    accountModal: { open: false, id: '', name: '', email: '', saving: false },

    clearMessages() {
      if (!confirm('确定要清空所有消息吗？')) return;
      this.msgs = [];
      this.saveToCache();
      this.showToast('消息已清空');
    },
    deleteMessage(idx) {
      this.msgs.splice(idx, 1);
      this.saveToCache();
      this.showToast('消息已删除');
    },
    resendMessage(idx) {
      if (this.busy) return;
      const msg = this.msgs[idx];
      if (!msg || msg.role !== 'user') return;
      this.draft = msg.content || '';
      this.selectedImages = [...(msg.images || [])];
      this.msgs = this.msgs.slice(0, idx);
      this.saveToCache();
      this.resizeTa();
      this.$nextTick(() => this.send());
    },

    init() {
      this.loadFromCache();
      this.checkAuth();
      this.$watch('cfg', () => this.saveToCache(), { deep: true });
      this.$watch('model', () => this.saveToCache());
      document.addEventListener('click', () => this.openSelect = null);
    },
    async checkAuth() {
      try {
        const r = await fetch('/auth/status');
        this.authed = r.ok;
      } catch (e) { this.authed = false }
      this.authChecked = true;
      if (this.authed) this.loadAuthedData();
    },
    loadAuthedData() {
      this.loadModels();
      this.loadStats();
      this.loadAccounts();
      this.loadRotation();
      this.loadApiKeys();
    },
    async loginAdmin() {
      if (!this.login.password || this.login.busy) return;
      this.login.busy = true;
      this.login.error = '';
      try {
        const r = await fetch('/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: this.login.password })
        });
        if (!r.ok) {
          let message = '密码错误';
          try {
            const d = await r.json();
            message = d.detail?.message || d.detail || message;
          } catch (e) { }
          throw new Error(message);
        }
        this.authed = true;
        this.login.password = '';
        this.loadAuthedData();
      } catch (e) { this.login.error = e.message || '登录失败' }
      finally { this.login.busy = false }
    },
    async logoutAdmin() {
      await fetch('/auth/logout', { method: 'POST' });
      this.authed = false;
      this.view = 'chat';
    },
    loadFromCache() {
      try {
        const msgs = localStorage.getItem('asp_msgs');
        if (msgs) this.msgs = JSON.parse(msgs);
        const cfg = localStorage.getItem('asp_cfg');
        if (cfg) Object.assign(this.cfg, JSON.parse(cfg));
        const model = localStorage.getItem('asp_model');
        if (model) this.model = model;
        const models = localStorage.getItem('asp_models');
        if (models) this.models = JSON.parse(models);
        this.apiKeyInput = localStorage.getItem('asp_api_key') || '';
      } catch (e) { console.error('Cache load error', e); }
    },
    saveToCache() {
      try {
        localStorage.setItem('asp_msgs', JSON.stringify(this.msgs));
        localStorage.setItem('asp_cfg', JSON.stringify(this.cfg));
        localStorage.setItem('asp_model', this.model);
        localStorage.setItem('asp_models', JSON.stringify(this.models));
      } catch (e) { console.error('Cache save error', e); }
    },
    clearCache() {
      if (!confirm('确定要清理本地缓存（聊天历史和配置）吗？')) return;
      localStorage.removeItem('asp_msgs');
      localStorage.removeItem('asp_cfg');
      localStorage.removeItem('asp_model');
      localStorage.removeItem('asp_models');
      location.reload();
    },
    go(v) { this.view = v; this.sidebarOpen = false; if (v === 'dashboard') this.loadStats(); if (v === 'accounts') { this.loadAccounts(); this.loadRotation() } if (v === 'settings') this.loadApiKeys() },
    showToast(m) { this.toast.msg = m; this.toast.show = true; if (this.toast.t) clearTimeout(this.toast.t); this.toast.t = setTimeout(() => this.toast.show = false, 3000) },
    toggleSelect(k, e) { e.stopPropagation(); this.openSelect = this.openSelect === k ? null : k },
    selectOpt(k, model, val) { this[model] = val; this.openSelect = null },
    authHeaders(extra = {}) {
      const headers = { ...extra };
      const key = this.apiKeyInput.trim();
      if (key) headers.Authorization = `Bearer ${key}`;
      return headers;
    },
    saveApiKey() {
      const key = this.apiKeyInput.trim();
      if (!key) { this.showToast('请输入 API Key'); return; }
      localStorage.setItem('asp_api_key', key);
      this.apiKeyInput = key;
      this.showToast('API Key 已保存');
    },
    clearApiKey() {
      localStorage.removeItem('asp_api_key');
      this.apiKeyInput = '';
      this.showToast('API Key 已清除');
    },
    renderMarkdown(text) {
      if (!text) return '';
      let html = text;

      // 1. 预处理数学公式，防止被 Marked 误解析
      const mathBlocks = [];
      // 处理块级公式 $$...$$
      html = html.replace(/\$\$([\s\S]+?)\$\$/g, (match, formula) => {
        const id = `__MATH_BLOCK_${mathBlocks.length}__`;
        try {
          mathBlocks.push({ id, html: katex.renderToString(formula.trim(), { displayMode: true, throwOnError: false }) });
          return id;
        } catch (e) { return match; }
      });
      // 处理行内公式 $...$
      html = html.replace(/\$([^\$\n]+?)\$/g, (match, formula) => {
        const id = `__MATH_INLINE_${mathBlocks.length}__`;
        try {
          mathBlocks.push({ id, html: katex.renderToString(formula.trim(), { displayMode: false, throwOnError: false }) });
          return id;
        } catch (e) { return match; }
      });

      // 2. 配置 Marked 并解析
      if (typeof marked !== 'undefined') {
        marked.setOptions({
          highlight: function (code, lang) {
            if (typeof hljs !== 'undefined' && lang && hljs.getLanguage(lang)) {
              try { return hljs.highlight(code, { language: lang }).value; } catch (e) { }
            }
            return code;
          },
          breaks: true,
          gfm: true
        });
        html = marked.parse(html);
      }

      // 3. 将公式替换回来
      mathBlocks.forEach(item => {
        html = html.replace(item.id, item.html);
      });

      // 4. 清洗并返回
      if (typeof DOMPurify !== 'undefined') {
        return DOMPurify.sanitize(html, { ADD_TAGS: ["math", "style"], ADD_ATTR: ["style"] });
      }
      return html;
    },

    async loadModels() { try { const r = await fetch('/settings/models'); const d = await r.json(); this.models = d.data || []; if (!this.model && this.models.length) this.model = this.models[0].id; this.saveToCache(); } catch (e) { } },
    async loadStats() { try { const r = await fetch('/stats'); const d = await r.json(); this.stats = d.models || {} } catch (e) { } },
    async loadAccounts() {
      try {
        const accountsResp = await fetch('/accounts');
        this.accounts = accountsResp.ok ? await accountsResp.json() : [];
        const activeResp = await fetch('/accounts/active');
        if (activeResp.ok) {
          const active = await activeResp.json();
          this.activeId = active?.id || '';
          this.activeAccount = active || {};
        } else {
          this.activeId = '';
          this.activeAccount = {};
        }
      } catch (e) { }
    },
    async loadRotation() { try { const r = await fetch('/rotation'); const d = await r.json(); this.rotationMode = d.mode || 'round_robin'; this.rotCfg.mode = d.mode || 'round_robin'; this.rotCfg.cooldown = d.cooldown_seconds || 60; this.rotationAccounts = d.accounts || {} } catch (e) { } },
    async loadApiKeys() { try { const r = await fetch('/settings/api-keys'); const d = await r.json(); this.apiKeys = d.keys || [] } catch (e) { } },

    get accountRows() { return this.accounts.map(a => ({ ...a, ...(this.rotationAccounts[a.id] || {}) })) },
    get totalReqs() { return Object.values(this.stats).reduce((s, v) => s + (v.requests || 0), 0) },
    get totalRL() { return Object.values(this.stats).reduce((s, v) => s + (v.rate_limited || 0), 0) },

    async saveRotation() { try { await fetch('/rotation/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: this.rotCfg.mode, cooldown_seconds: this.rotCfg.cooldown }) }); this.showToast('已保存'); this.loadRotation() } catch (e) { this.showToast('保存失败') } },
    async forceNext() { try { await fetch('/rotation/next', { method: 'POST' }); this.showToast('已切换账号'); this.loadAccounts() } catch (e) { this.showToast('切换失败') } },
    async activateAccount(id) { try { await fetch(`/accounts/${id}/activate`, { method: 'POST' }); this.showToast('已激活'); this.loadAccounts(); this.loadRotation() } catch (e) { this.showToast('激活失败') } },
    editAccount(account) {
      this.accountModal.id = account.id;
      this.accountModal.name = account.name || '';
      this.accountModal.email = account.email || '';
      this.accountModal.open = true;
    },
    async saveAccountMeta() {
      const id = this.accountModal.id;
      const name = this.accountModal.name.trim();
      const email = this.accountModal.email.trim();
      if (!id) return;
      if (!name) { this.showToast('账号名称不能为空'); return; }
      this.accountModal.saving = true;
      try {
        const r = await fetch(`/accounts/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, email })
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || '保存失败');
        this.accountModal.open = false;
        this.showToast('账号已更新');
        await this.loadAccounts();
        await this.loadRotation();
      } catch (e) { this.showToast(e.message || '保存失败') }
      finally { this.accountModal.saving = false }
    },
    async deleteAccount(id) {
      const account = this.accounts.find(a => a.id === id);
      const label = account?.email || account?.name || id;
      if (!confirm(`确定删除账号 ${label} 吗？`)) return;
      try {
        const r = await fetch(`/accounts/${id}`, { method: 'DELETE' });
        if (!r.ok) throw new Error('delete failed');
        this.showToast('已删除账号');
        await this.loadAccounts();
        await this.loadRotation();
      } catch (e) { this.showToast('删除失败') }
    },
    async exportAccount(id) {
      try {
        const r = await fetch(`/accounts/${id}/export`);
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'export failed');
        const account = d.account || {};
        const label = (account.email || account.name || id).replace(/[^A-Za-z0-9_.-]+/g, '_') || 'account';
        const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `aistudio-account-${label}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        this.showToast('已导出账号');
      } catch (e) { this.showToast(e.message || '导出失败') }
    },
    async importAccountFile(e) {
      const file = e.target.files?.[0];
      e.target.value = '';
      if (!file) return;
      try {
        const payload = JSON.parse(await file.text());
        const r = await fetch('/accounts/import-account', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ package: payload })
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || '导入失败');
        this.showToast(`已导入账号: ${d.name}`);
        await this.loadAccounts();
        await this.loadRotation();
      } catch (err) { this.showToast(err.message || '导入失败') }
    },
    async addAccount() { try { const r = await fetch('/accounts/login/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) }); this.showToast(r.ok ? '登录已开始！' : '启动登录失败') } catch (e) { this.showToast('网络错误') } },
    async importCookies() {
      const raw = this.cookieModal.cookies.trim();
      if (!raw) { this.showToast('请输入 Cookie'); return }
      // 支持多行：每行一个 cookie 或用分号分隔
      const cookies = raw.split(/[\r\n]+/).map(l => l.trim()).filter(Boolean).join('; ');
      this.cookieModal.importing = true;
      try {
        const body = { cookies };
        if (this.cookieModal.name.trim()) body.name = this.cookieModal.name.trim();
        if (this.cookieModal.email.trim()) body.email = this.cookieModal.email.trim();
        const r = await fetch('/accounts/import-cookies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json();
        if (r.ok) {
          this.showToast(`导入成功: ${d.cookie_count} 个 cookie`);
          this.cookieModal.open = false; this.cookieModal.cookies = ''; this.cookieModal.name = ''; this.cookieModal.email = '';
          this.loadAccounts(); this.loadRotation();
        } else {
          this.showToast(d.detail || '导入失败');
        }
      } catch (e) { this.showToast('网络错误') }
      finally { this.cookieModal.importing = false }
    },
    async createApiKey() {
      const name = this.newKeyName.trim() || '默认 Key';
      try {
        const r = await fetch('/settings/api-keys', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name })
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || '创建失败');
        this.createdKey = d.key;
        this.apiKeyInput = d.key;
        localStorage.setItem('asp_api_key', d.key);
        this.newKeyName = '默认 Key';
        await this.loadApiKeys();
        this.showToast('Key 已创建');
      } catch (e) { this.showToast(e.message || '创建失败') }
    },
    async deleteApiKey(id) {
      if (!confirm('确定删除这个 API Key 吗？')) return;
      try {
        const r = await fetch(`/settings/api-keys/${id}`, { method: 'DELETE' });
        if (!r.ok) throw new Error('删除失败');
        await this.loadApiKeys();
        this.showToast('Key 已删除');
      } catch (e) { this.showToast(e.message || '删除失败') }
    },

    resizeTa() { const el = this.$refs.ta; el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 200) + 'px' },
    scrollDown() { setTimeout(() => { const el = document.getElementById('chat-scroll'); if (el) el.scrollTop = el.scrollHeight }, 50) },

    async handleImageUpload(e) {
      const files = Array.from(e.target.files);
      for (const f of files) {
        if (!f.type.startsWith('image/')) continue;
        const reader = new FileReader();
        reader.onload = (ev) => this.selectedImages.push(ev.target.result);
        reader.readAsDataURL(f);
      }
      e.target.value = '';
    },
    removeImage(idx) { this.selectedImages.splice(idx, 1) },

    async send() {
      const t = this.draft.trim(); const imgs = [...this.selectedImages]; if (!t && !imgs.length) return; if (this.busy || !this.model) return;
      if (!this.apiKeyInput.trim()) {
        this.showToast('请先在设置页创建并保存 API Key');
        this.view = 'settings';
        this.loadApiKeys();
        return;
      }
      this.msgs.push({ role: 'user', content: t, images: imgs }); this.draft = ''; this.selectedImages = []; this.busy = true; this.resizeTa(); this.scrollDown(); this.saveToCache();

      // 生图模型走 /v1/images/generations
      if (this.model.includes('image')) {
        try {
          const r = await fetch('/v1/images/generations', { method: 'POST', headers: this.authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ model: this.model, prompt: t, size: '1024x1024' }) });
          if (!r.ok) { let e = r.statusText; try { const d = await r.json(); if (d.detail) e = JSON.stringify(d.detail) } catch (x) { }; this.msgs.push({ role: 'assistant', content: '', error: `Error ${r.status}: ${e}` }) }
          else {
            const d = await r.json(); const imgs = d.data || []; let content = ''; imgs.forEach(img => { if (img.b64_json) content += `![image](data:image/png;base64,${img.b64_json})\n`; else if (img.url) content += `![image](${img.url})\n`; if (img.revised_prompt) content += img.revised_prompt + '\n' });
            this.msgs.push({ role: 'assistant', content: content || '(无响应内容)', showThinking: false })
          }
        }
        catch (e) { this.msgs.push({ role: 'assistant', content: '', error: e.message }) }
        finally { this.busy = false; this.scrollDown(); this.saveToCache() }
        return;
      }

      const messages = this.msgs.map(m => {
        if (m.images && m.images.length) {
          const parts = [{ type: 'text', text: m.content || '' }];
          m.images.forEach(img => parts.push({ type: 'image_url', image_url: { url: img } }));
          return { role: m.role, content: parts };
        }
        return { role: m.role, content: m.content };
      });

      const body = { model: this.model, messages };
      if (this.cfg.temperature !== 1) body.temperature = this.cfg.temperature;
      if (this.cfg.topP !== 1) body.top_p = this.cfg.topP;
      if (this.cfg.maxTokens !== 8192) body.max_tokens = this.cfg.maxTokens;
      if (this.cfg.stream === 'on') body.stream = true;
      if (this.cfg.thinking !== 'off') body.thinking = this.cfg.thinking;
      this.saveToCache();
      if (this.cfg.search === 'on') body.google_search = true;
      if (this.cfg.safety === 'off') body.safety_off = true;

      try {
        const r = await fetch('/v1/chat/completions', { method: 'POST', headers: this.authHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(body) });
        if (!r.ok) { let e = r.statusText; try { const d = await r.json(); if (d.detail) e = JSON.stringify(d.detail) } catch (x) { }; this.msgs.push({ role: 'assistant', content: '', error: `Error ${r.status}: ${e}` }) }
        else if (this.cfg.stream === 'on') {
          const reader = r.body.getReader(); const dec = new TextDecoder(); this.msgs.push({ role: 'assistant', content: '', thinking: '', showThinking: false }); const idx = this.msgs.length - 1; let buf = '';
          while (true) {
            const { done, value } = await reader.read(); if (done) break; buf += dec.decode(value, { stream: true }); const lines = buf.split('\n'); buf = lines.pop();
            for (const ln of lines) {
              if (ln.startsWith('data: ') && ln !== 'data: [DONE]') {
                try {
                  const d = JSON.parse(ln.slice(6)); const delta = d.choices?.[0]?.delta || {};
                  const c = delta.content; if (c) this.msgs[idx].content += c;
                  const th = delta.reasoning_content || delta.thinking || delta.reasoning; if (th) this.msgs[idx].thinking += th;
                } catch (e) { }
              }
            }
            this.scrollDown()
          }
          this.saveToCache();
        } else {
          const d = await r.json(); const msg = d.choices?.[0]?.message || {};
          this.msgs.push({ role: 'assistant', content: msg.content || '(无响应内容)', thinking: msg.reasoning_content || msg.thinking || msg.reasoning || '', showThinking: false })
        }
      }
      catch (e) { this.msgs.push({ role: 'assistant', content: '', error: e.message }) }
      finally { this.busy = false; this.scrollDown(); this.saveToCache() }
    },

    fmtDate(s) { if (!s) return '-'; try { return new Date(s).toLocaleString() } catch (e) { return s } }
  }
}
