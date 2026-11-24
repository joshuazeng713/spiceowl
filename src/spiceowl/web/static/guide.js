/* Explanatory controls only. No execution, connection or mutable account state. */
import { design, pool } from './guide-data.js';
const root=document.getElementById('product-guide');
const byId=id=>document.getElementById(id);
function group(selector,handler){const buttons=[...root.querySelectorAll(selector)];buttons.forEach(b=>{if(!b.hasAttribute('aria-pressed'))b.setAttribute('aria-pressed','false');});buttons.forEach(b=>b.addEventListener('click',()=>{buttons.forEach(x=>x.setAttribute('aria-pressed',String(x===b)));handler(b);}));}
const modules={
 library:['电路库：给 Agent 和人相同的入口','当前公开清单有 4 个 library、98 个 cell。按单元声明拥有的文件和共享依赖；同一依赖保留一个物理源，避免修改了副本却没有影响顶层电路。'],
 rnl:['RNL：本地文件里的电路可以被直接看见','RNL 源或网表内的 @RNL 描述几何；浏览器 / Wasm 呈现原理图。Agent 可以一起编辑电气源与图形描述。只改网表并不自动证明历史几何已同步，也不意味着自动完成芯片版图。'],
 agent:['Agent：直接处理设计资料，而不是遥控远端 GUI','当前电路的 library、cell、文件和源哈希进入会话；Agent 经已配置的库工具读写本地源、写 TB、检查差异。普通设计模式与开发工作台应用的原生开发模式分开。'],
 git:['Git：单元级设计历史就在工作台里','文件、历史、提交–PR 和 Testbench CI 跟随所选 cell。先保存本地 checkpoint，再提交明确文件；恢复产生新工作改动。显式 PR 发布需要本机 GitHub 登录，不自动合并。'],
 ci:['Testbench CI：把已经定义的实验变成回归','Agent 编写测试平台与后处理，用户确认目标。选择注册 TB 绑定到 cell；源检查不需要 PDK，实际 Spectre 回归需要配置过的许可、模型、执行环境。'],
 simulation:['SpiceOwl：越过仿真边界时使用的管理工具','在同一 Agent 对话里检查服务器与 PDK、组织测试平台、观察运行和分析结果。CircuitGit 的现有 Runner 执行冻结的实验，SpiceOwl 不再创建平行会话或第二套执行记录。']
};
const agentSteps=[
 ['先有本机执行环境，再连接网页。','网页负责交互，本机助手负责访问 checkout、Git 和 Codex。纯 GitHub Pages 是公开浏览入口；没有助手时，不会拥有本机电路和模型权限。','从 Git checkout 启动本机助手，再在网页选择“连接本机 Codex”。'],
 ['检查账号，连接一个空闲会话。','Help → AI / MCP 连接，检查发现的 Codex 程序、账号状态和模型目录。已有登录由本机运行时管理；目录可读不代表当前模型推理一定可用。','不把账号 token 放进网页；连接状态与模型实际回答分别验证。'],
 ['选择模型、思考强度和操作权限。','输入框齿轮里选择可用模型和实际支持的强度。只读会话只看资料；写电路还需要操作员配置的库访问能力。模型运行、待审或结果未知时保留被冻结的设置。','会话设置只影响这个通道；不会改写本机 Codex 全局配置。'],
 ['绑定当前电路，并用一次可审查的任务开始。','选中 library / cell 后使用“使用当前电路”。Agent 获得精确源身份，再读文件、提议修改并给出 diff。仿真上下文和工具结果同步回工作台。','先读 → 设计与成组保存 → 看 diff / 版本 → 确认需要后再提交实验。']
];
if(root){
 group('[data-circuit-view]',b=>{byId('native-circuit').src='./design-'+b.dataset.circuitView+'.svg';byId('native-circuit').alt=b.dataset.circuitView==='buffer'?'RNL 实际渲染的两级缓冲器':'RNL 实际渲染的原始反相器';});
 group('[data-module]',b=>{const v=modules[b.dataset.module];byId('module-title').textContent=v[0];byId('module-copy').textContent=v[1];});
 group('[data-agent-step]',b=>{const v=agentSteps[+b.dataset.agentStep];['agent-step-title','agent-step-copy','agent-step-output'].forEach((id,i)=>byId(id).textContent=v[i]);});
 group('[data-dev-mode]',b=>{byId('dev-mode-copy').textContent=b.dataset.devMode==='full'?'full：操作员明确开放后，新的开发线程拥有原生命令、文件与网络能力，不逐项批准。完全访问不限于起始目录；不改变普通电路工具的规则。':'manual：原生 Codex 从只读沙箱开始，需要写入或越过沙箱的具体操作由用户在网页批准。';});
 const designViews={
  read:['CMOS_INVERTER · 原始 2-MOS 单元\n\nM9  (CLKN CLK VSS VSS) nch_ulvt_mac\nM10 (CLKN CLK VDD VDD) pch_ulvt_mac\n\n源文件 SHA-256\n'+(design?.source_sha256||''),'实际 read_file 取得的源；文件同时含网表与 @RNL 几何。'],
  save:['+ M11 (BUFOUT CLKN VSS VSS) nch_ulvt_mac\n+ M12 (BUFOUT CLKN VDD VDD) pch_ulvt_mac\n+ 新输出端口 BUFOUT\n+ 第二级 @PLACE / @WIRE\n\nexecute(save) → '+(design?.saved||''),'实际增加第二级，并一起写入对应几何描述。当前只做静态源检查，不推断电气性能。'],
  commit:['branch: example/two-stage-buffer\n\nbaseline '+design?.baseline_commit+'\nvariant  '+design?.variant_commit+'\n\nexecute(commit) → '+design?.committed,'实际 Git 版本已保存在隔离仓库；没有远端 push 或仿真。'],
  restore:['execute(restore, checkpoint_version="before")\nstate: '+design?.restored+'\n\n恢复后的内容哈希 = 原始内容哈希\n后续设计提交仍保留：'+design?.history_preserved+'\n未执行 git reset','恢复产生一个新的工作改动。它不会删除刚才的设计提交，之后仍可比较和再次使用。']
 };
 function showDesign(mode){if(byId('design-code')){byId('design-code').textContent=designViews[mode][0];byId('design-note').textContent=designViews[mode][1];}}
 group('[data-design]',b=>showDesign(b.dataset.design));showDesign('read');
 function renderPool(){if(!byId('pool-cpu'))return;const key=byId('pool-cpu').value+'-'+byId('pool-memory').value;const s=pool.scenarios[key];byId('pool-result-title').textContent=s.recommended?'推荐 Server A':'没有满足本次要求的候选';byId('pool-result-copy').textContent=s.recommended?'A 满足资源与时间检查；B 因 CPU / load 排除，C 的资源快照已过 120 秒。推荐不做预留，提交前还需复核。':'80 GiB 请求会让 A 的剩余内存低于总内存的 20% 保留线；B 忙、C 过期。应降低合理需求、扩充候选或重新观测，不能把未知状态当空闲。';byId('pool-host-a').textContent=s.recommended?'符合本次资源要求':'内存保留余量不足';byId('pool-host-b').textContent='CPU / load 余量不足';byId('pool-host-c').textContent='资源快照过期';}
 byId('pool-cpu')?.addEventListener('change',renderPool);byId('pool-memory')?.addEventListener('change',renderPool);renderPool();
 group('[data-pdk-mode]',b=>{const cached=b.dataset.pdkMode==='cached';byId('pdk-title').textContent=cached?'先查明确范围的本地缓存，再核对当前环境。':'先找到授权范围内的真实入口。';byId('pdk-copy').textContent=cached?'按服务器身份、根目录与观测指纹查询 SQLite LUT。已索引器件可以跨电路组合复用；miss / stale 要明确处理。缓存命中不发 SSH，也不能代替提交前依赖审计。':'读取环境线索、扫描指定 PDK 根、解析 include / section 和模型包装。对找不到或有歧义的器件返回缺项；由 Agent 提议下一步或请用户补充范围。';});
 const scenarios={disconnect:{v:['running · 上次确认','unknown','unknown','unknown','保留原依赖快照'],title:'先恢复观测，不先重提任务。',copy:'最后确认状态为 running，当前 SSH 查询失败。Agent 应保留 run / key，查看最近成功观测与原任务状态，不能把连接失败解释成求解器失败。',action:'下一步：查询原任务并核对日志。不要立即 resubmit。'},target:{v:['completed','valid · 条件满足','fail · 未达到冻结目标','unknown','verified within scope'],title:'有效结果未达标，需要改设计或实验方案。',copy:'波形可读、测量前提满足，但指标未达到原先冻结目标。Agent 可以比较版本、检查机理并提议下一次实验；不能把目标降低后把这次运行改写为通过。',action:'下一步：保留失败的规格结论，提出带明确改动的新实验。'},soa:{v:['completed','valid · 当前测量范围','单独比较规格','violations detected','按实际依赖记录'],title:'求解器跑完了，但工作条件仍可能超限。',copy:'SOA / 模型诊断与功能指标独立。真实 Bootstrap 例子中有 CMI-2752 端电压告警；Agent 应检查偏置、模型适用域和缺少的证据，而不是仅看 SNDR 或退出码。',action:'下一步：核对超限实例与原始条件，为后续实验提出可审阅修改。'}};
 function showDiagnosis(name){if(!byId('diagnostic-status'))return;const s=scenarios[name],labels=['执行状态','测量有效性','功能 / 性能','工作条件合格性','依赖来源'];byId('diagnostic-status').replaceChildren(...labels.map((label,i)=>{const row=document.createElement('div');const span=document.createElement('span');span.textContent=label;const b=document.createElement('b');b.textContent=s.v[i];if(/unknown|fail|violations/.test(s.v[i]))b.className='unknown';row.append(span,b);return row;}));byId('diagnostic-title').textContent=s.title;byId('diagnostic-copy').textContent=s.copy;byId('diagnostic-action').textContent=s.action;}
 group('[data-diagnosis]',b=>showDiagnosis(b.dataset.diagnosis));showDiagnosis('disconnect');
 root.querySelectorAll('[data-reference]').forEach(a=>a.addEventListener('click',()=>byId('sc-reference').open=true));
 const reveal=()=>{const id=location.hash.slice(1);if(id&&/^(stage-|public-sources|responsibility|provenance)/.test(id)){byId('sc-reference').open=true;document.getElementById(id)?.scrollIntoView();}};reveal();window.addEventListener('hashchange',reveal);if(new URLSearchParams(location.search).has('stage'))byId('sc-reference').open=true;
}
