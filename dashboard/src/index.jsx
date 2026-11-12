import { translate, resolveLocale } from './i18n.js';
(() => {
  'use strict';

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const React = SDK.React;
  const {
    useState,
    useEffect,
    useRef,
    useMemo
  } = React;
  const API = '/api/plugins/model-studio';
  const LEVELS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];
  const FORMAT = {
    chat_completions: 'OpenAI Chat Completions',
    codex_responses: 'OpenAI Responses',
    anthropic_messages: 'Anthropic Messages'
  };
  const empty = revision => ({
    id: '',
    revision,
    name: '',
    note: '',
    base_url: '',
    api_mode: 'chat_completions',
    has_key: false,
    models: [],
    default_model: '',
    editable: true
  });
  const seed = row => ({
    id: row.id,
    context_length: null,
    max_output_tokens: null,
    reasoning_mode: 'auto',
    reasoning_effort: null,
    thinking_budget: null,
    effort_levels: [],
    metadata: row.metadata || {
      source: 'unknown'
    }
  });
  const copy = value => JSON.parse(JSON.stringify(value));
  const message = err => typeof err?.message === 'string' ? err.message : '请求失败，请重试。';
  const nformat = (value, locale) => typeof value === 'number' ? value.toLocaleString(resolveLocale(locale)) + (resolveLocale(locale) === 'zh' ? ' Token' : ' tokens') : translate('未知 / 自动', locale);
  const changed = (a, b) => JSON.stringify(a) !== JSON.stringify(b);
  const post = (path, body) => SDK.fetchJSON(API + path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Hermes-Model-Studio': '1'
    },
    body: JSON.stringify(body)
  });
  function Icon({
    name,
    size = 18
  }) {
    const paths = {
      plus: 'M12 5v14M5 12h14',
      check: 'm5 12 4 4L19 6',
      arrow: 'M5 12h14m-6-6 6 6-6 6',
      close: 'm6 6 12 12M6 18 18 6',
      search: 'm21 21-4.5-4.5M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0',
      shield: 'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Zm-4 9 3 3 5-6',
      key: 'M14 7a4 4 0 1 0 3 7l4-4-2-2-2 2-1-1M3 21l7-7',
      refresh: 'M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5',
      server: 'M4 3h16v7H4V3Zm0 11h16v7H4v-7ZM7 6h.1M7 17h.1',
      chevron: 'm8 10 4 4 4-4',
      trash: 'M3 6h18M9 6V3h6v3m-10 0 1 15h12l1-15M10 10v7m4-7v7',
      bolt: 'm13 2-9 12h7l-1 8 10-13h-7l0-7'
    };
    return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.server} /></svg>;
  }
  function Button({
    children,
    kind = '',
    busy = false,
    ...props
  }) {
    return <button type="button" className={'hms-button ' + kind} {...props}>{busy ? <span className="hms-spinner" /> : null}{children}</button>;
  }
  function Dialog({
    title,
    children,
    confirm = null,
    danger = false,
    onConfirm,
    onClose,
    busy = false
  }) {
    const {
      locale
    } = SDK.useI18n();
    const t = value => translate(value, locale);
    const root = useRef(null),
      prior = useRef(null);
    useEffect(() => {
      prior.current = document.activeElement;
      const el = root.current;
      el.querySelector('button')?.focus();
      const key = e => {
        if (e.key === 'Escape' && !busy) onClose();
        if (e.key === 'Tab') {
          const all = [...el.querySelectorAll('button:not(:disabled),input,select')];
          const first = all[0],
            last = all.at(-1);
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }
      };
      document.addEventListener('keydown', key);
      return () => {
        document.removeEventListener('keydown', key);
        prior.current?.focus();
      };
    }, [busy]);
    return <div className="hms-overlay" onMouseDown={e => {
      if (e.target === e.currentTarget && !busy) onClose();
    }}><div ref={root} className="hms-dialog" role="dialog" aria-modal="true" aria-labelledby="hms-dialog-title"><div className="hms-dialog-head"><h2 id="hms-dialog-title">{title}</h2><Button aria-label={t("关闭对话框")} onClick={onClose} disabled={busy}><Icon name="close" /></Button></div><div className="hms-dialog-body">{children}</div><div className="hms-dialog-actions"><Button onClick={onClose} disabled={busy}>{t("取消")}</Button><Button kind={danger ? 'hms-danger' : 'hms-primary'} onClick={onConfirm} disabled={busy} busy={busy}>{confirm ?? t("确认")}</Button></div></div></div>;
  }
  function Field({
    label,
    hint,
    children
  }) {
    return <label className="hms-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
  }
  function NumberInput({
    value,
    onChange,
    placeholder,
    max
  }) {
    return <input type="number" min="1" max={max} step="1" value={value ?? ''} placeholder={placeholder} onChange={e => onChange(e.target.value === '' ? null : Number(e.target.value))} />;
  }
  function ModelCard({
    model,
    format,
    expanded,
    onToggle,
    onChange,
    onRemove
  }) {
    const {
      locale
    } = SDK.useI18n();
    const t = value => translate(value, locale);
    const m = model,
      meta = m.metadata || {},
      source = t({
        endpoint: '接口返回',
        catalog: '模型目录匹配',
        unknown: '规格未知'
      }[meta.source] || '规格未知');
    const levels = m.effort_levels?.length ? m.effort_levels : meta.effort_levels || [];
    const patch = data => onChange({
      ...m,
      ...data
    });
    return <article className="hms-model-card"><div className="hms-model-top"><button className="hms-model-title" onClick={onToggle} aria-expanded={expanded}><span className="hms-model-symbol"><Icon name="bolt" size={16} /></span><span className="hms-truncate">{m.id}</span><span className={'hms-chevron ' + (expanded ? 'open' : '')}><Icon name="chevron" size={16} /></span></button><button type="button" className="hms-icon-btn" aria-label={t("移除模型 ") + m.id} onClick={onRemove}><Icon name="close" size={16} /></button></div><div className="hms-model-summary"><span>{source}</span><span>{t("上下文 ")}{nformat(m.context_length || meta.context_length, locale)}</span>{m.reasoning_mode !== 'auto' && <span className="hms-accent">{t("思考已覆盖")}</span>}</div>{expanded && <div className="hms-model-settings"><div className="hms-form-grid"><Field label={t("上下文窗口")} hint={t("自动值：") + nformat(meta.context_length, locale)}><NumberInput value={m.context_length} onChange={v => patch({
              context_length: v
            })} placeholder={t("留空自动识别")} max={4000000} /></Field><Field label={t("单次最大输出")} hint={t("自动上限：") + nformat(meta.max_output_tokens, locale)}><NumberInput value={m.max_output_tokens} onChange={v => patch({
              max_output_tokens: v
            })} placeholder={t("留空沿用 Hermes")} max={1000000} /></Field></div><Field label={t("默认思考策略")} hint={meta.supports_reasoning === false ? t("目录标记不支持思考。手动覆盖未经此接口验证。") : t("只对这个服务和模型生效；不改变其他模型。")}><select value={m.reasoning_mode} onChange={e => patch({
            reasoning_mode: e.target.value
          })}><option value="auto">{t("自动 · 沿用 Hermes 原生策略")}</option><option value="off">{t("关闭 · 无法关闭时使用最低可用等级")}</option><option value="omit">{t("不发送 · 使用上游默认值（不代表关闭）")}</option><option value="effort">{t("指定思考等级")}</option>{format === 'anthropic_messages' && <option value="budget">{t("指定思考 Token 预算")}</option>}</select></Field>{m.reasoning_mode === 'effort' && <><Field label={t("默认思考等级")} hint={!levels.length ? t("暂无已知等级，请先勾选你确认支持的选项。") : t("目录信息并非接口实测结果；不支持的等级可能被上游拒绝。")}><select value={m.reasoning_effort || ''} onChange={e => patch({
              reasoning_effort: e.target.value || null
            })}><option value="">{t("请选择")}</option>{[...new Set([...levels, ...(m.reasoning_effort ? [m.reasoning_effort] : [])])].map(x => <option key={x} value={x}>{x}</option>)}</select></Field><fieldset className="hms-levels"><legend>{t("可用等级 · 手动覆盖")}</legend>{LEVELS.map(x => <label key={x}><input type="checkbox" checked={levels.includes(x)} onChange={e => patch({
                effort_levels: e.target.checked ? [...levels, x] : levels.filter(v => v !== x)
              })} />{x}</label>)}</fieldset></>}{m.reasoning_mode === 'budget' && <Field label={t("思考预算（Token）")} hint={t("至少 1024；同时填写最大输出，并为回答保留至少 1024 Token。仅用于支持此参数的 Anthropic 兼容接口。")}><NumberInput value={m.thinking_budget} onChange={v => patch({
            thinking_budget: v
          })} placeholder={t("例如 8192")} max={1000000} /></Field>}<div className="hms-model-foot"><span>{t("手动填写优先；留空使用自动值。")}</span><Button kind="hms-link" onClick={() => patch({
            context_length: null,
            max_output_tokens: null,
            reasoning_mode: 'auto',
            reasoning_effort: null,
            thinking_budget: null,
            effort_levels: []
          })}>{t("恢复自动")}</Button></div></div>}</article>;
  }
  function App() {
    const {
      locale
    } = SDK.useI18n();
    const t = value => translate(value, locale);
    const [data, setData] = useState(null),
      [form, setForm] = useState(empty('')),
      [baseline, setBaseline] = useState(empty('')),
      [apiKey, setApiKey] = useState(''),
      [remote, setRemote] = useState([]),
      [search, setSearch] = useState(''),
      [selectedSearch, setSelectedSearch] = useState(''),
      [manual, setManual] = useState(''),
      [expanded, setExpanded] = useState(''),
      [busy, setBusy] = useState('load'),
      [error, setError] = useState(''),
      [notice, setNotice] = useState(''),
      [dialog, setDialog] = useState(null);
    const sequence = useRef(0),
      mounted = useRef(true),
      keyRef = useRef('');
    const dirty = changed(form, baseline) || Boolean(apiKey);
    useEffect(() => {
      mounted.current = true;
      reload();
      return () => {
        mounted.current = false;
        sequence.current++;
        keyRef.current = '';
      };
    }, []);
    useEffect(() => {
      keyRef.current = apiKey;
    }, [apiKey]);
    useEffect(() => {
      if (!dirty) return;
      const fn = e => {
        e.preventDefault();
        e.returnValue = '';
      };
      window.addEventListener('beforeunload', fn);
      return () => window.removeEventListener('beforeunload', fn);
    }, [dirty]);
    const install = (row, info = data) => {
      sequence.current++;
      const next = copy(row || empty(info?.revision || ''));
      setForm(next);
      setBaseline(copy(next));
      setApiKey('');
      keyRef.current = '';
      setRemote([]);
      setSearch('');
      setSelectedSearch('');
      setExpanded('');
      setManual('');
      setError('');
    };
    async function reload(preferred) {
      setBusy('load');
      setError('');
      try {
        const info = await SDK.fetchJSON(API + '/providers');
        if (!mounted.current) return;
        setData(info);
        install(info.providers.find(x => x.id === preferred) || info.providers.find(x => x.is_current) || info.providers[0] || null, info);
      } catch (e) {
        if (mounted.current) setError(message(e));
      } finally {
        if (mounted.current) setBusy('');
      }
    }
    const askChange = fn => {
      if (dirty) {
        setDialog({
          type: 'discard',
          action: fn
        });
      } else fn();
    };
    const update = patch => {
      sequence.current++;
      setForm(f => ({
        ...f,
        ...patch
      }));
      setError('');
      setNotice('');
    };
    const updateModel = (mid, next) => update({
      models: form.models.map(m => m.id === mid ? next : m)
    });
    const inputKey = value => {
      sequence.current++;
      setApiKey(value);
      keyRef.current = value;
      setNotice('');
    };
    const connection = () => ({
      id: form.id,
      revision: form.revision,
      base_url: form.base_url,
      api_mode: form.api_mode,
      api_key: apiKey || null
    });
    async function discover() {
      setBusy('discover');
      setError('');
      setNotice('');
      const seq = ++sequence.current;
      try {
        const result = await post('/discover', connection());
        if (!mounted.current || sequence.current !== seq) return;
        setRemote(result.models || []);
        const byId = new Map((result.models || []).map(x => [x.id, x.metadata]));
        setForm(f => ({
          ...f,
          models: f.models.map(m => ({
            ...m,
            metadata: byId.get(m.id) || m.metadata
          }))
        }));
        setNotice(result.warning || '模型列表已更新。');
      } catch (e) {
        if (mounted.current && sequence.current === seq) setError(message(e));
      } finally {
        if (mounted.current) setBusy('');
      }
    }
    function toggle(row) {
      if (form.models.some(m => m.id === row.id)) {
        update({
          models: form.models.filter(m => m.id !== row.id),
          default_model: form.default_model === row.id ? '' : form.default_model
        });
      } else if (form.models.length >= 200) {
        setError('最多添加 200 个模型。');
      } else {
        update({
          models: [...form.models, seed(row)],
          default_model: form.default_model || row.id
        });
      }
    }
    async function addManual() {
      const id = manual.trim();
      if (!id) return;
      if (form.models.some(x => x.id === id)) {
        setError('这个模型已经添加。');
        return;
      }
      if (id.length > 160 || form.models.length >= 200) {
        setError('模型 ID 过长或列表已满。');
        return;
      }
      const row = seed({
        id
      });
      update({
        models: [...form.models, row],
        default_model: form.default_model || id
      });
      setManual('');
      setExpanded(id);
      if (!form.base_url) return;
      const seq = sequence.current;
      try {
        const result = await post('/metadata', {
          base_url: form.base_url,
          api_mode: form.api_mode,
          ids: [id]
        });
        if (!mounted.current || sequence.current !== seq) return;
        const meta = result.models?.[0]?.metadata;
        if (meta) setForm(f => ({
          ...f,
          models: f.models.map(m => m.id === id ? {
            ...m,
            metadata: meta
          } : m)
        }));
      } catch {/* Manual IDs remain usable when metadata is unavailable. */}
    }
    function validate() {
      if (!form.name.trim()) return '请填写服务名称。';
      if (!form.base_url.startsWith('https://')) return '请填写 HTTPS 公网接口地址。';
      if (!form.models.length) return '请至少添加一个模型。';
      if (!form.models.some(x => x.id === form.default_model)) return '请选择已添加的模型作为服务默认模型。';
      for (const m of form.models) {
        if (m.context_length && m.max_output_tokens >= m.context_length) return m.id + '：最大输出必须小于上下文。';
        if (m.reasoning_mode === 'effort' && !m.reasoning_effort) return m.id + '：请选择思考等级。';
        if (m.reasoning_mode === 'budget' && (!m.thinking_budget || m.thinking_budget < 1024 || !m.max_output_tokens || m.thinking_budget + 1024 > m.max_output_tokens)) return m.id + '：思考预算至少 1024，并为回答保留 1024 Token。';
      }
      return '';
    }
    async function save() {
      const invalid = validate();
      if (invalid) {
        setError(invalid);
        return;
      }
      setBusy('save');
      setError('');
      setNotice('');
      try {
        const result = await post('/save', {
          ...form,
          api_key: apiKey || null,
          clear_key: false
        });
        setApiKey('');
        keyRef.current = '';
        await reload(result.id);
        setNotice('服务已保存。没有自动切换当前默认模型。');
      } catch (e) {
        setError(message(e));
        setApiKey('');
        keyRef.current = '';
      } finally {
        setBusy('');
      }
    }
    async function test() {
      setDialog(null);
      setBusy('test');
      setError('');
      setNotice('');
      try {
        const result = await post('/test', {
          ...connection(),
          model: form.default_model
        });
        setNotice(result.message + ' · ' + result.latency_ms + ' ms');
      } catch (e) {
        setError(message(e));
      } finally {
        setBusy('');
      }
    }
    async function mutate(type) {
      setBusy(type);
      setError('');
      try {
        await post('/' + type, {
          id: form.id,
          revision: form.revision,
          model: form.default_model,
          confirm: true
        });
        setDialog(null);
        await reload(type === 'activate' ? form.id : undefined);
        setNotice(type === 'activate' ? '默认模型已更新。已有会话可能仍沿用原路由；新会话使用新配置。' : '服务已删除。');
      } catch (e) {
        setDialog(null);
        setError(message(e));
      } finally {
        setBusy('');
      }
    }
    const filtered = remote.filter(m => m.id.toLowerCase().includes(search.toLowerCase()));
    const selected = form.models.filter(m => m.id.toLowerCase().includes(selectedSearch.toLowerCase()));
    const disabled = Boolean(busy) || form.editable === false;
    const addressChanged = form.has_key && (form.base_url !== baseline.base_url || form.api_mode !== baseline.api_mode);
    return <main className="hms-root"><header className="hms-heading"><div><div className="hms-eyebrow">HERMES / MODEL STUDIO</div><h1>{t("模型服务")}</h1></div><div className="hms-secure-badge"><Icon name="shield" />{t("密钥仅保存在服务器")}</div></header><div className="hms-statusbar"><span className="hms-dot" /><span>{t("当前默认")}</span><strong>{data?.current?.model || t("读取中…")}</strong><span className="hms-status-provider">{data?.current?.provider}</span><Button kind="hms-link" disabled={!!busy} onClick={() => askChange(() => reload(form.id))}><Icon name="refresh" size={14} />{t("刷新配置")}</Button></div>{error && <div className="hms-banner hms-error" role="alert">{t(error)}<button aria-label={t("关闭错误提示")} onClick={() => setError('')}><Icon name="close" size={16} /></button></div>}{notice && <div className="hms-banner hms-success" role="status"><Icon name="check" />{t(notice)}</div>}{busy === 'load' && !data ? <div className="hms-initial"><span className="hms-spinner" />{t("正在读取服务配置…")}</div> : <div className="hms-layout"><aside className="hms-sidebar"><div className="hms-sidebar-title"><h2>{t("我的服务 ")}<span>{data?.providers?.length || 0}</span></h2><button className="hms-icon-btn" aria-label={t("新增服务")} disabled={!!busy} onClick={() => askChange(() => install(null))}><Icon name="plus" /></button></div><div className="hms-service-list">{data?.providers?.map(p => <button key={p.id} disabled={!!busy} className={'hms-service ' + (form.id === p.id ? 'selected' : '')} onClick={() => askChange(() => install(p))}><span className="hms-service-icon"><Icon name="server" /></span><span className="hms-service-info"><strong>{p.name}</strong><small>{(() => {
                    try {
                      return new URL(p.base_url).hostname;
                    } catch {
                      return t("接口地址");
                    }
                  })()}</small></span>{p.is_current && <span className="hms-current-dot" title={t("当前默认服务")} />}</button>)}</div><Button kind={'hms-new ' + (!form.id ? 'active' : '')} disabled={!!busy} onClick={() => askChange(() => install(null))}><Icon name="plus" />{t("添加自定义服务")}</Button><div className="hms-sidebar-note"><Icon name="shield" size={17} /><p>{t("原有手机连接与推送接口保持不变。只有明确切换默认模型，才会改变共享配置。")}</p></div></aside><section className="hms-editor"><div className="hms-section-heading"><div><h2>{form.id ? t("编辑服务") : t("新建服务")}</h2><p>{form.id ? t("修改服务连接与已添加的模型。") : t("支持 OpenAI 与 Anthropic 兼容的公网 HTTPS 接口。")}</p></div>{dirty && <span className="hms-draft-badge">{t("未保存")}</span>}</div>{form.warning && <div className="hms-inline-note">{t(form.warning)}</div>}<fieldset disabled={disabled} className="hms-connection"><div className="hms-form-grid"><Field label={t("服务名称")}><input maxLength="100" value={form.name} placeholder={t("例如：我的中转服务")} onChange={e => update({
                  name: e.target.value
                })} /></Field><Field label={t("接口格式")}><select value={form.api_mode} onChange={e => update({
                  api_mode: e.target.value
                })}>{Object.entries(FORMAT).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field></div><Field label="Base URL"><input type="url" maxLength="500" autoComplete="off" spellCheck="false" value={form.base_url} placeholder="https://api.example.com/v1" onChange={e => update({
                base_url: e.target.value
              })} /></Field><div className="hms-form-grid"><Field label={<span>{t("API 密钥 ")}{form.has_key && <span className="hms-key-state"><Icon name="check" size={12} />{t("已配置")}</span>}</span>} hint={form.has_key ? t("留空保持当前密钥；输入新值即可替换。") : t("仅通过 HTTPS 提交，不会在保存后回显。")}><input type={form.has_key && !apiKey ? "password" : "text"} autoComplete={form.has_key ? "off" : "new-password"} maxLength="4096" value={apiKey} placeholder={form.has_key ? t("已安全保存 · 留空不修改") : t("输入 API Key（无认证接口可留空）")} onChange={e => inputKey(e.target.value)} /></Field><Field label={t("备注（可选）")}><input maxLength="400" value={form.note || ''} placeholder={t("方便区分用途，不要填写密钥")} onChange={e => update({
                  note: e.target.value
                })} /></Field></div>{addressChanged && <div className="hms-warning">{t("地址或接口格式已变化。请重新输入密钥；含额外请求头的服务需要新建，不能自动转发旧凭据。")}</div>}<div className="hms-connection-actions"><Button disabled={disabled || !form.base_url} busy={busy === 'discover'} onClick={discover}><Icon name="refresh" size={16} />{t("获取模型列表")}</Button><Button disabled={disabled || !form.default_model || !form.base_url} onClick={() => setDialog({
                type: 'test'
              })}><Icon name="bolt" size={16} />{t("测试连接")}</Button></div></fieldset><div className="hms-model-panes"><section className="hms-pane"><div className="hms-pane-heading"><h3>{t("该服务的模型 ")}<span>{remote.length}</span></h3><label className="hms-search"><Icon name="search" size={15} /><input aria-label={t("搜索可用模型")} value={search} onChange={e => setSearch(e.target.value)} placeholder={t("搜索模型 ID")} /></label></div><div className="hms-available-list">{busy === 'discover' ? <div className="hms-empty"><span className="hms-spinner" /><strong>{t("正在获取模型")}</strong><p>{t("服务器正在安全连接你的接口。")}</p></div> : remote.length ? filtered.length ? filtered.map(row => <label key={row.id} className="hms-available"><input type="checkbox" disabled={disabled} checked={form.models.some(m => m.id === row.id)} onChange={() => toggle(row)} /><span><strong>{row.id}</strong><small>{row.metadata?.context_length ? nformat(row.metadata.context_length, locale) + t(" 上下文") : t("规格未识别")}{row.metadata?.source === 'catalog' ? t(" · 目录匹配") : ''}</small></span><Icon name="plus" size={14} /></label>) : <div className="hms-empty"><strong>{t("没有匹配的模型")}</strong><p>{t("试试其他关键词。")}</p></div> : <div className="hms-empty"><span className="hms-empty-icon"><Icon name="server" size={30} /></span><strong>{t("从你的接口获取模型")}</strong><p>{t("填写地址和密钥，点击“获取模型列表”。")}<br />{t("也可以在右侧直接添加模型 ID。")}</p></div>}</div></section><section className="hms-pane"><div className="hms-pane-heading"><h3>{t("已添加模型 ")}<span>{form.models.length}</span></h3><label className="hms-search"><Icon name="search" size={15} /><input aria-label={t("搜索已添加模型")} value={selectedSearch} onChange={e => setSelectedSearch(e.target.value)} placeholder={t("搜索已添加模型")} /></label></div><fieldset disabled={disabled} className="hms-selected-list">{!form.models.length ? <div className="hms-empty"><span className="hms-empty-icon"><Icon name="check" size={30} /></span><strong>{t("选择你要使用的模型")}</strong><p>{t("左侧勾选添加，点击模型展开参数设置。")}</p></div> : selected.length ? selected.map(m => <ModelCard key={m.id} model={m} format={form.api_mode} expanded={expanded === m.id} onToggle={() => setExpanded(expanded === m.id ? '' : m.id)} onChange={next => updateModel(m.id, next)} onRemove={() => toggle(m)} />) : <div className="hms-empty"><strong>{t("没有匹配的模型")}</strong></div>}</fieldset><div className="hms-manual"><input aria-label={t("自定义模型 ID")} maxLength="160" value={manual} onChange={e => setManual(e.target.value)} onKeyDown={e => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addManual();
                  }
                }} disabled={disabled} placeholder={t("输入模型 ID，例如 my-model-v2")} /><Button disabled={disabled || !manual.trim()} onClick={addManual}><Icon name="plus" size={16} />{t("添加")}</Button></div></section></div><div className="hms-footer"><Field label={t("此服务的默认模型")} hint={t("保存仅记录服务；点击“设为全局默认”才改变 Hermes 默认路由。")}><select disabled={disabled || !form.models.length} value={form.default_model} onChange={e => update({
                default_model: e.target.value
              })}><option value="">{t("请选择模型")}</option>{form.models.map(m => <option key={m.id} value={m.id}>{m.id}</option>)}</select></Field><div className="hms-footer-actions"><Button kind="hms-danger-link" disabled={disabled || !form.id || form.is_current} onClick={() => setDialog({
                type: 'delete'
              })}><Icon name="trash" size={15} />{t("删除服务")}</Button><span className="hms-action-spacer" /><Button disabled={disabled || !form.id || dirty || !form.default_model} onClick={() => setDialog({
                type: 'activate'
              })}>{t("设为全局默认")}</Button><Button kind="hms-primary" disabled={disabled || !dirty} busy={busy === 'save'} onClick={save}><Icon name="check" size={16} />{t("保存服务")}</Button></div></div><div className="hms-bottom-note"><Icon name="shield" size={15} /><span>{t("密钥不回显 · 能力来源可追溯 · 请求参数只对本页管理的模型生效。上下文修改用于新会话；已有会话可能保留原设置。")}</span></div></section></div>}{dialog?.type === 'discard' && <Dialog title={t("放弃未保存的修改？")} confirm={t("放弃并继续")} onClose={() => setDialog(null)} onConfirm={() => {
        dialog.action();
        setDialog(null);
      }}><p>{t("当前表单和未保存的密钥会被清空。服务器上的配置不变。")}</p></Dialog>}{dialog?.type === 'test' && <Dialog title={t("发送最小模型测试？")} confirm={t("开始测试")} onClose={() => setDialog(null)} onConfirm={test}><p>{t("将向 ")}<strong>{form.default_model}</strong>{t(" 发送一条“Reply with OK.”请求，输出上限 64 Token，未启用任何工具。")}</p><p>{t("这可能产生少量模型费用，不会写入聊天记录。")}</p></Dialog>}{dialog?.type === 'activate' && <Dialog title={t("切换全局默认模型？")} confirm={t("确认切换")} busy={busy === 'activate'} onClose={() => setDialog(null)} onConfirm={() => mutate('activate')}><p>{t("默认模型将切换为 ")}<strong>{form.default_model}</strong>{t("。")}</p><p>{t("这是共享配置，可能影响网页、官方手机端及其他渠道的新会话。已有会话不会强制重置。")}</p></Dialog>}{dialog?.type === 'delete' && <Dialog title={t("删除这个模型服务？")} confirm={t("确认删除")} danger busy={busy === 'delete'} onClose={() => setDialog(null)} onConfirm={() => mutate('delete')}><p>{t("删除 ")}<strong>{form.name}</strong>{t(" 及插件创建的专用密钥。不会删除聊天记录。")}</p><p>{t("正在使用或被其他路由引用的服务，服务器会拒绝删除。")}</p></Dialog>}</main>;
  }
  window.__HERMES_PLUGINS__.register('model-studio', App);
})();
