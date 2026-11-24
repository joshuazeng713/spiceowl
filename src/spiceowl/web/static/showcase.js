/* Read-only visual guide. No live endpoint, storage or execution action. */
const story = document.querySelector('#visual-story');
const steps = [
 ['把电路和问题放在一起','从 Library 选择电路，确认端口、源版本和测试目标。自举开关的 VBST 是栅压控制输出；真正做频谱分析的是测试平台中的 VOUT。','选择 DUT 与目标','源版本 + 端口身份'],
 ['先把实验条件说清楚','检查服务器上的 Spectre、可用资源和 PDK section。Agent 可协助写测试平台；工具检查依赖、激励、负载、保存信号和分析方法。','确认环境、PDK 与 TB','依赖检查 + 冻结输入'],
 ['读过输入，再执行','在原向导里审阅冻结的测试平台和目标。人工审核或已有明确范围的操作授权交给 CircuitGit Runner。保留提交身份，连接断开后先核对原运行。','审阅具体实验','运行身份 + 执行记录'],
 ['先看原始波形，再算指标','收集原生 PSF，核对样本数量、采样间隔与分析类型。SpiceOwl 的解析与分析工具把波形变成指标；求解退出码不等于科学指标通过。','选运行、信号与测量','原始结果 + 测量条件'],
 ['让修改和结果成对出现','这个例子将外接采样管 multi 从 1 改到 16。两次结果分别保留，使用同一 FFT 条件比较。数值改善是这组实验的观察，尚不能替代噪声、PVT 或整机验证。','对比版本与实验','双运行证据 + 来源哈希']
];
if(story){
 const stepButtons=[...story.querySelectorAll('[data-sc-step]')];
 for(const b of stepButtons)b.addEventListener('click',()=>{const i=Number(b.dataset.scStep);stepButtons.forEach(x=>x.setAttribute('aria-pressed',String(x===b)));['sc-step-title','sc-step-copy','sc-human','sc-artifact'].forEach((id,k)=>document.getElementById(id).textContent=steps[i][k]);});
 for(const b of story.querySelectorAll('[data-sc-case]'))b.addEventListener('click',()=>{story.querySelectorAll('[data-sc-case]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));story.querySelectorAll('[data-sc-panel]').forEach(x=>x.hidden=x.dataset.scPanel!==b.dataset.scCase);});
 for(const b of story.querySelectorAll('[data-sc-spectrum]'))b.addEventListener('click',()=>{story.querySelectorAll('[data-sc-spectrum]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));document.getElementById('sc-spectrum').src='./showcase-spectrum'+(b.dataset.scSpectrum==='both'?'':'-'+b.dataset.scSpectrum)+'.svg';});
 story.querySelector('[data-sc-reference]')?.addEventListener('click',()=>{document.getElementById('sc-reference').open=true;});
 // Existing historical deep links keep their original destination visible.
 const revealReference=()=>{const id=location.hash.slice(1);if(id && !id.startsWith('sc-')){const target=document.getElementById(id);if(target?.closest('#sc-reference') || /^stage-/.test(id)){document.getElementById('sc-reference').open=true;target?.scrollIntoView();}}};
 revealReference();window.addEventListener('hashchange',revealReference);
 if(new URLSearchParams(location.search).has('stage'))document.getElementById('sc-reference').open=true;
}
