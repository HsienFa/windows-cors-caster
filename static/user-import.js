/* No file contents, passwords, preview tokens or CSRF values in persistent storage. */
(() => {
    let closeCurrent = null;
    let cancellation = Promise.resolve();
    const messages = {
        unauthorized: '登入已失效，請重新登入。', request_forbidden: '請重新開啟匯入視窗。',
        preview_expired: '預覽已取消、失效或使用過，請重新預覽。',
        file_changed: '檔案與預覽不同，請重新預覽。', account_conflict: '帳號已存在，請修改檔案後重新預覽。',
        duplicate_in_file: '檔案內帳號重複', username: '帳號須為 3–50 個允許字元',
        password: '密碼須為文字及 6–100 個允許字元', columns: '每列必須恰好兩欄',
        headers: '第 1 列必須是 username、password', file_type: '只支援 CSV 或 XLSX',
        file_size: '檔案須非空且不超過 512 KiB', empty_file: '沒有可匯入資料',
        row_or_column_limit: '僅限兩欄，最多掃描 501 列（含標題及空白列）',
        multiline_field: 'CSV 欄位不得包含換行', one_sheet_required: 'XLSX 必須只有一個工作表',
        unsupported_xlsx: '不支援公式、合併儲存格、外部連結或額外 XLSX 內容',
        xlsx_limits: 'XLSX 超過安全解析限制', invalid_xlsx: 'XLSX 結構不正確',
        invalid_file: '無法解析檔案，請使用範本格式', preview_capacity: '預覽容量已滿，請稍後再試',
        import_busy: '目前有其他匯入正在處理，請稍後再試', database_busy: '資料庫忙碌，請重新預覽',
        database_error: '資料庫操作未完成，請重新預覽', import_failed: '匯入未完成，請重新預覽',
        commit_result_unknown: '無法確認結果；可能已匯入。請先查使用者清單，不會自動重送。'
    };

    window.showUserImport = async function () {
        if (closeCurrent) closeCurrent();
        const modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.innerHTML = `<div class="modal-content" role="dialog" aria-label="批次匯入使用者">
            <h4>批次匯入使用者</h4><p data-rules></p>
            <button data-template="csv">下載 CSV 空白範本</button>
            <button data-template="xlsx">下載 XLSX 空白範本</button>
            <p><label>選擇匯入檔案 <input type="file" accept=".csv,.xlsx"></label></p>
            <button data-preview>預覽</button><button data-confirm disabled>確認整批匯入</button>
            <button data-close>取消／關閉</button>
            <p data-status role="status"></p><div data-results style="max-height:40vh;overflow:auto"></div></div>`;
        document.body.appendChild(modal);
        const input = modal.querySelector('input');
        const status = modal.querySelector('[data-status]');
        const confirm = modal.querySelector('[data-confirm]');
        const preview = modal.querySelector('[data-preview]');
        const close = modal.querySelector('[data-close]');
        let file = null, csrf = null, token = null, timer = null, ended = false, busy = false;
        const headers = () => ({'X-Import-Request': '1', 'X-Import-CSRF': csrf || ''});
        const request = (action, extra = {}) => fetch('/api/users/import/' + action, {
            method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: headers(), ...extra
        });
        const invalidate = () => { token = null; confirm.disabled = true; clearTimeout(timer); };
        const cancelRemote = () => {
            if (csrf) {
                const savedHeaders = headers();
                cancellation = cancellation.then(() => request('cancel', {headers: savedHeaders})).catch(() => {});
            }
            return cancellation;
        };
        const finish = () => {
            if (busy) return;
            ended = true; invalidate(); cancelRemote(); file = null; input.value = ''; csrf = null;
            modal.remove(); document.removeEventListener('click', navigation); closeCurrent = null;
        };
        const navigation = event => { if (event.target.closest('.nav-item') && !busy) finish(); };
        document.addEventListener('click', navigation);
        closeCurrent = finish;
        close.onclick = finish;
        input.onchange = () => {
            invalidate(); cancelRemote(); file = input.files[0] || null;
            modal.querySelector('[data-results]').replaceChildren(); status.textContent = '';
        };
        const lock = value => {
            busy = value; input.disabled = value; preview.disabled = value; close.disabled = value;
            modal.querySelectorAll('[data-template]').forEach(button => { button.disabled = value; });
        };
        try {
            const response = await request('context');
            const context = await response.json();
            if (!response.ok) throw new Error('unauthorized');
            if (ended) return;
            csrf = context.csrf;
            modal.querySelector('[data-rules]').textContent = context.rules + ' 預覽有效 5 分鐘。';
        } catch (_) { status.textContent = messages.unauthorized; preview.disabled = true; return; }
        modal.querySelectorAll('[data-template]').forEach(button => {
            button.onclick = async () => {
                try {
                    const response = await request('template-' + button.dataset.template);
                    if (!response.ok) throw new Error();
                    const url = URL.createObjectURL(await response.blob());
                    const link = document.createElement('a'); link.href = url;
                    link.download = 'users-template.' + button.dataset.template;
                    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
                } catch (_) { status.textContent = '無法下載範本，請確認登入狀態。'; }
            };
        });
        async function upload(action) {
            if (busy) return;
            const kind = file?.name.toLowerCase().split('.').pop();
            if (!file || !['csv', 'xlsx'].includes(kind) || !file.size || file.size > 512 * 1024) {
                status.textContent = '請選擇不超過 512 KiB 的 CSV／XLSX 檔案。'; return;
            }
            const previous = token; invalidate(); lock(true);
            status.textContent = action === 'confirm' ? '正在匯入，請勿關閉頁面…' : '正在預覽…';
            try {
                // A previous selection's cancellation must finish before a new preview.
                await cancellation;
                const response = await request(action, {headers: {...headers(),
                    'Content-Type': 'application/octet-stream', 'X-Import-Format': kind,
                    'X-Import-Preview': previous || ''}, body: file});
                const result = await response.json();
                const rows = modal.querySelector('[data-results]'); rows.replaceChildren();
                for (const row of result.rows || []) {
                    const line = document.createElement('p');
                    line.textContent = `第 ${row.row} 列｜${row.username || '帳號格式錯誤'}｜` +
                        (row.errors.map(code => messages[code] || '資料不正確').join('；') || '可匯入');
                    rows.appendChild(line);
                }
                if (result.error) { status.textContent = messages[result.error] || '操作失敗，請重新預覽。'; }
                else if (action === 'confirm' && response.ok) {
                    status.textContent = `已匯入 ${result.imported} 筆。`; file = null; input.value = '';
                    loadPageContent('users');
                } else {
                    status.textContent = `合格 ${result.valid_count} 筆。` +
                        (result.can_import ? '請確認帳號後按「確認整批匯入」。' : '有錯誤，整批不匯入。');
                    if (response.ok && result.can_import) {
                        token = result.preview; confirm.disabled = false;
                        timer = setTimeout(() => { invalidate(); cancelRemote(); file = null; input.value = '';
                            status.textContent = messages.preview_expired; }, result.expires_in * 1000);
                    }
                }
                if (!response.ok) { file = null; input.value = ''; cancelRemote(); }
            } catch (_) {
                status.textContent = action === 'confirm'
                    ? '無法確認結果；可能已匯入。請先查使用者清單，不會自動重送。'
                    : '預覽失敗，請重新選擇檔案。';
                file = null; input.value = ''; cancelRemote();
            } finally { lock(false); }
        }
        preview.onclick = () => upload('preview');
        confirm.onclick = () => upload('confirm');
    };
    window.addEventListener('pagehide', () => { if (closeCurrent) closeCurrent(); });
})();
