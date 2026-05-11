// ==UserScript==
// @name         AI Studio Cookie 提取器
// @namespace    http://tampermonkey.net/
// @version      2.0
// @description  一键提取 Google AI Studio 的 Cookies（包括 HttpOnly）用于 aistudio-api 项目
// @author       You
// @match        https://aistudio.google.com/*
// @grant        GM_setClipboard
// @grant        GM_notification
// @grant        GM_cookie
// @run-at       document-end
// ==/UserScript==

(function() {
    'use strict';

    console.log('[AI Studio Cookie 提取器] 脚本已加载');

    let buttonCreated = false;

    // 创建提取按钮
    function createButton() {
        if (buttonCreated) {
            console.log('[AI Studio Cookie 提取器] 按钮已存在，跳过创建');
            return;
        }

        if (!document.body) {
            console.log('[AI Studio Cookie 提取器] body 不存在，等待...');
            setTimeout(createButton, 100);
            return;
        }

        // 检查是否已存在按钮
        if (document.getElementById('aistudio-cookie-btn')) {
            console.log('[AI Studio Cookie 提取器] 按钮已存在');
            buttonCreated = true;
            return;
        }

        const btn = document.createElement('button');
        btn.id = 'aistudio-cookie-btn';
        btn.textContent = '📦 导出账号 JSON';

        // 使用内联样式避免 CSP 问题
        Object.assign(btn.style, {
            position: 'fixed',
            top: '20px',
            right: '20px',
            zIndex: '999999',
            padding: '12px 20px',
            background: '#667eea',
            color: 'white',
            border: 'none',
            borderRadius: '8px',
            fontSize: '14px',
            fontWeight: '600',
            cursor: 'pointer',
            boxShadow: '0 4px 15px rgba(0,0,0,0.2)',
            transition: 'all 0.3s ease'
        });

        btn.onmouseover = () => {
            btn.style.transform = 'translateY(-2px)';
            btn.style.boxShadow = '0 6px 20px rgba(0,0,0,0.3)';
        };
        btn.onmouseout = () => {
            btn.style.transform = 'translateY(0)';
            btn.style.boxShadow = '0 4px 15px rgba(0,0,0,0.2)';
        };
        btn.onclick = extractAndCopy;

        document.body.appendChild(btn);
        buttonCreated = true;
        console.log('[AI Studio Cookie 提取器] 按钮已创建');
    }

    // 提取并复制 Cookies（包括 HttpOnly）
    async function extractAndCopy() {
        console.log('[AI Studio Cookie 提取器] 开始提取 Cookies');

        try {
            // 使用 GM_cookie API 获取所有 cookies（包括 HttpOnly）
            const allCookies = await new Promise((resolve, reject) => {
                GM_cookie.list({}, (cookies, error) => {
                    if (error) reject(error);
                    else resolve(cookies);
                });
            });

            console.log('[AI Studio Cookie 提取器] 获取到', allCookies.length, '个 cookies');

            // 过滤 Google 相关的 cookies
            const googleCookies = allCookies.filter(c =>
                c.domain.includes('google.com') ||
                c.domain.includes('youtube.com')
            );

            console.log('[AI Studio Cookie 提取器] 过滤后', googleCookies.length, '个 Google cookies');

            if (googleCookies.length === 0) {
                showNotification('❌ 未找到 Google Cookies', 'error');
                return;
            }

            function normalizeSameSite(value) {
                const v = String(value || 'None').toLowerCase();
                if (v === 'lax') return 'Lax';
                if (v === 'strict') return 'Strict';
                return 'None';
            }

            // 构造 Playwright storage_state 格式
            const storageState = {
                cookies: googleCookies.map(c => ({
                    name: c.name,
                    value: c.value,
                    domain: c.domain,
                    path: c.path || '/',
                    expires: c.expirationDate ? Math.floor(c.expirationDate) : -1,
                    httpOnly: c.httpOnly || false,
                    secure: c.secure || false,
                    sameSite: normalizeSameSite(c.sameSite)
                })),
                origins: []
            };

            const accountPackage = {
                version: 1,
                type: 'aistudio-api-account',
                account: {
                    name: '导入的账号',
                    email: null
                },
                storage_state: storageState
            };

            const jsonStr = JSON.stringify(accountPackage, null, 2);

            // 创建下载
            const blob = new Blob([jsonStr], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `aistudio-account-${Date.now()}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            showNotification(`✅ 已下载 ${googleCookies.length} 个 Cookies`, 'success');
            console.log('[AI Studio Cookie 提取器] 下载成功');
        } catch (e) {
            console.error('[AI Studio Cookie 提取器] 提取失败:', e);
            showNotification('❌ 提取失败: ' + e.message, 'error');
        }
    }

    // 显示通知
    function showNotification(message, type) {
        if (typeof GM_notification !== 'undefined') {
            GM_notification({
                text: message,
                timeout: 3000
            });
        }

        // 页面内通知
        const toast = document.createElement('div');
        toast.textContent = message;
        toast.style.cssText = `
            position: fixed;
            top: 80px;
            right: 20px;
            z-index: 10000;
            padding: 12px 20px;
            background: ${type === 'success' ? '#10b981' : '#ef4444'};
            color: white;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            animation: slideIn 0.3s ease;
        `;
        document.body.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // 添加动画样式
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(400px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOut {
            from { transform: translateX(0); opacity: 1; }
            to { transform: translateX(400px); opacity: 0; }
        }
    `;
    document.head.appendChild(style);

    // 多种方式确保按钮创建
    function init() {
        console.log('[AI Studio Cookie 提取器] 初始化，readyState:', document.readyState);
        createButton();

        // 监听 SPA 路由变化
        let lastUrl = location.href;
        new MutationObserver(() => {
            const url = location.href;
            if (url !== lastUrl) {
                lastUrl = url;
                console.log('[AI Studio Cookie 提取器] URL 变化，重新创建按钮');
                buttonCreated = false;
                setTimeout(createButton, 500);
            }
        }).observe(document.body, { subtree: true, childList: true });
    }

    // 多重保险机制
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // 额外的延迟创建
    setTimeout(createButton, 1000);
    setTimeout(createButton, 3000);

    console.log('[AI Studio Cookie 提取器] 脚本初始化完成');
})();
