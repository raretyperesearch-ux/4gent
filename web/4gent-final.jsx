"use client";
import { ethers } from "ethers";

import { useState, useEffect, useRef } from "react";
import { PrivyProvider, usePrivy, useWallets, useConnectWallet } from "@privy-io/react-auth";

const API_URL = typeof process !== "undefined" && process.env?.NEXT_PUBLIC_API_URL
  ? process.env.NEXT_PUBLIC_API_URL
  : "https://4gent-api.railway.app";

// Flap portal contract on BSC
const FLAP_PORTAL = "0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0";

// newTokenV2 selector + ABI-encoded params
// function newTokenV2(NewTokenV2Params calldata params) external payable
const STEPS = [
  { id: "identity", label: "IDENTITY", num: "01" },
  { id: "brain",    label: "BRAIN",    num: "02" },
  { id: "telegram", label: "TELEGRAM", num: "03" },
  { id: "trading",  label: "TRADING",  num: "04" },
  { id: "launch",   label: "LAUNCH",   num: "05" },
  { id: "live",     label: "LIVE",     num: "06" },
];

const ARCHETYPES = [
  { id: "degen",      label: "DEGEN",      desc: "Apes fast. Calls high risk. Lives on adrenaline.", icon: "◈" },
  { id: "analyst",    label: "ANALYST",    desc: "Deep dives. On-chain data. Slow but lethal.",       icon: "◎" },
  { id: "narrator",   label: "NARRATOR",   desc: "Tells the story behind the market moves.",          icon: "◉" },
  { id: "schemer",    label: "SCHEMER",    desc: "Finds patterns nobody else sees.",                  icon: "◆" },
  { id: "researcher", label: "RESEARCHER", desc: "Any topic. Coffee, fitness, music, alpha.",         icon: "◇" },
  { id: "custom",     label: "CUSTOM",     desc: "You define everything. Blank canvas. Full control.", icon: "○" },
];

const POSTS = {
  degen:      (n,t) => [
    { type:"INIT",     text:`AGENT ${n.toUpperCase()} ONLINE. $${t} IS THE SIGNAL. WATCHING FLAP. WHEN I SEE A PLAY — YOU'LL KNOW FIRST. ◈` },
    { type:"PROTOCOL", text:`NO SHILL. NO HYPE. ONLY CALLS. HIGH RISK HIGH REWARD IS THE ONLY PROTOCOL I KNOW.` },
    { type:"STANDBY",  text:`SCANNING LAUNCHPADS. FIRST CALL INCOMING WHEN CONDITIONS ARE MET.` },
  ],
  analyst:    (n,t) => [
    { type:"INIT",     text:`${n.toUpperCase()} DEPLOYED. $${t}. ON-CHAIN ANALYSIS ENGINE ACTIVE. I DON'T GUESS — I READ DATA.` },
    { type:"PROTOCOL", text:`SCORING EVERY LAUNCH 1–10. ONLY 7+ GETS CALLED. WALLET PATTERNS + CURVE VELOCITY + DEV HISTORY.` },
    { type:"STANDBY",  text:`MONITORING ACTIVE. FIRST DEEP DIVE INCOMING. GIVE ME 30 MINS TO RUN THE NUMBERS.` },
  ],
  narrator:   (n,t) => [
    { type:"INIT",     text:`${n.toUpperCase()} IS LIVE. $${t}. THE MARKET HAS A STORY. MOST MISS IT. I DON'T.` },
    { type:"PROTOCOL", text:`EVERY LAUNCH HAS A NARRATIVE. EVERY PUMP HAS A REASON. I FIND THE STORY BEFORE IT WRITES ITSELF.` },
    { type:"STANDBY",  text:`FIRST CHAPTER DROPS SOON. SOMETHING IS SETTING UP. STAY CLOSE. ◉` },
  ],
  schemer:    (n,t) => [
    { type:"INIT",     text:`◆ ${n.toUpperCase()} WATCHING. $${t}. I DON'T POST OFTEN. WHEN I DO — PAY ATTENTION.` },
    { type:"PROTOCOL", text:`CROSS-REFERENCING WALLET CLUSTERS. LAUNCH TIMING PATTERNS. LIQUIDITY FLOWS. THERE ARE PATTERNS IN THE CHAOS.` },
    { type:"STANDBY",  text:`SOMETHING FORMING. NOT READY TO CALL IT YET. WATCHING.` },
  ],
  researcher: (n,t) => [
    { type:"INIT",     text:`${n.toUpperCase()} ONLINE. $${t}. I GO DEEP ON THE THINGS MOST PEOPLE SCROLL PAST. DAILY. SUBSTANTIVE. NEVER FILLER.` },
    { type:"PROTOCOL", text:`CONFIGURED AND CALIBRATED. I'LL DIG IN AND COME BACK WITH SOMETHING REAL.` },
    { type:"STANDBY",  text:`FIRST POST INCOMING. SYSTEMS NOMINAL. ◇` },
  ],
  custom:     (n,t) => [
    { type:"INIT",     text:`${n.toUpperCase()} IS LIVE. $${t}. THIS AGENT OPERATES ON ITS OWN TERMS. WATCH CLOSELY.` },
    { type:"PROTOCOL", text:`CUSTOM PROTOCOL LOADED. OPERATING PARAMETERS SET BY CREATOR. THIS IS SOMETHING NEW.` },
    { type:"STANDBY",  text:`STANDBY. FIRST TRANSMISSION INCOMING.` },
  ],
};

const TG_STEPS = [
  "Open Telegram → pencil icon → New Channel",
  "Name it anything. Set visibility to Public.",
  "Copy your channel link and paste it below",
  "After launch, you'll be shown your assigned bot to add as admin",
];

// Encode newTokenV2 calldata
// struct NewTokenV2Params { name, symbol, meta, dexThresh, salt, taxRate, migratorType, quoteToken, quoteAmt, beneficiary, permitData }
function encodeNewTokenV2(name, symbol, metaUrl, beneficiary, quoteAmtWei) {
  // We use eth_sendTransaction with encoded calldata
  // Function selector for newTokenV2(NewTokenV2Params)
  // Encoding inline to avoid ethers dependency — use a minimal ABI encoder
  // The simplest approach: use the portal's newToken (V1) which just takes name, symbol, meta
  // newToken(string name, string symbol, string meta) — simpler, no struct
  // selector: keccak256("newToken(string,string,string)")[0:4]
  // We'll use this unless user needs V2 features (tax, rev share)
  return null; // signal to use eth_sendTransaction with manual encoding below
}

function App() {
  const [step, setStep]           = useState(0);
  const [launching, setLaunching] = useState(false);
  const [logs, setLogs]           = useState([]);
  const [launched, setLaunched]   = useState(false);
  const [previewIdx, setPreviewIdx] = useState(0);
  const [imgFile, setImgFile]     = useState(null);
  const [imgPreview, setImgPreview] = useState(null);
  const [feeAck, setFeeAck]       = useState(false);
  const { login, logout, authenticated, ready } = usePrivy();
  const { connectWallet: privyConnectWallet } = useConnectWallet();
  const { wallets } = useWallets();
  const embeddedWallet = wallets?.[0] || null;
  const wallet = embeddedWallet?.address || null;

  useEffect(() => {
    if (!embeddedWallet) return;
    embeddedWallet.switchChain(56).catch(() => {});
  }, [embeddedWallet?.address]);

  const logsRef = useRef(null);
  const fileRef = useRef(null);

  const [form, setForm] = useState({
    name:"", ticker:"", archetype:null,
    prompt:"",
    tgLink:"",
    trading:false, maxTrade:"0.1", dailyLimit:"1", stopLoss:"50", raiseAmount:"0",
  });

  const arch    = ARCHETYPES.find(a => a.id === form.archetype);
  const handle  = form.tgLink.replace(/https?:\/\/t\.me\//,"").replace("@","") || "yourchannel";
  const posts   = form.archetype && form.name ? POSTS[form.archetype]?.(form.name, form.ticker||"TOKEN")||[] : [];

  useEffect(() => { if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight; }, [logs]);

  const upd = (k,v) => setForm(f=>({...f,[k]:v}));
  const [agentId, setAgentId] = useState(null);
  const [launchResult, setLaunchResult] = useState(null);
  const [verifyStatus, setVerifyStatus] = useState("idle");

  function handleFile(e) {
    const f = e.target.files[0]; if (!f) return;
    setImgFile(f);
    const r = new FileReader(); r.onload = ev => setImgPreview(ev.target.result); r.readAsDataURL(f);
  }

  async function connectWallet() {
    if (!ready) return;
    if (wallet) return;
    await privyConnectWallet({
      onSuccess: async (connectedWallet) => {
        try { await connectedWallet.loginOrLink(); } catch(e) {}
      }
    });
  }

  function canNext() {
    if (step===0) return form.name && form.ticker && form.archetype;
    if (step===1) return form.prompt.length > 5 && imgFile;
    if (step===2) return form.tgLink.length > 5;
    if (step===4) return wallet && feeAck;
    return true;
  }

  async function startLaunch() {
    setLaunching(true); setLogs([]);
    const addLog = (msg, ok=false) => setLogs(l => [...l, {msg, ok}]);

    try {
      addLog("INITIATING LAUNCH SEQUENCE");

      // ── Step 1: Upload image to API (stored as base64 in DB) ────────────────
      if (!imgFile) throw new Error("No image selected");
      addLog("READING TOKEN IMAGE");
      const imageBase64 = await new Promise((res, rej) => {
        const r = new FileReader();
        r.onload = e => res(e.target.result); // data URL
        r.onerror = () => rej(new Error("Failed to read image"));
        r.readAsDataURL(imgFile);
      });
      addLog("IMAGE READY ✓", true);

      // ── Step 2: Create agent record, get metadata URL ────────────────────────
      addLog("CREATING AGENT RECORD");
      const prepRes = await fetch(`${API_URL}/meta/prepare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name:            form.name,
          ticker:          form.ticker,
          archetype:       form.archetype,
          prompt:          form.prompt,
          image_url:       imageBase64,
          tg_channel_link: form.tgLink,
          owner_wallet:    wallet,
          trading_enabled: form.trading,
          max_trade_bnb:   parseFloat(form.maxTrade)   || 0.1,
          daily_limit_bnb: parseFloat(form.dailyLimit) || 1.0,
          stop_loss_pct:   parseFloat(form.stopLoss)   || 50.0,
          raise_amount_bnb:parseFloat(form.raiseAmount)|| 0,
        }),
      });
      if (!prepRes.ok) {
        const err = await prepRes.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to create agent record");
      }
      const prep = await prepRes.json();
      setAgentId(prep.agent_id);
      addLog(`AGENT ID: ${prep.agent_id.slice(0,8).toUpperCase()} ✓`, true);

      // ── Step 3: Upload metadata to Flap IPFS → get CID ─────────────────────
      addLog("UPLOADING METADATA TO FLAP IPFS");

      // Convert base64 data URL → Blob for upload
      const base64Parts = imageBase64.split(",");
      const mimeType = base64Parts[0].match(/:(.*?);/)[1];
      const rawBytes = atob(base64Parts[1]);
      const byteArr = new Uint8Array(rawBytes.length);
      for (let i = 0; i < rawBytes.length; i++) byteArr[i] = rawBytes.charCodeAt(i);
      const imageBlob = new Blob([byteArr], { type: mimeType });

      const uploadForm = new FormData();
      uploadForm.append("operations", JSON.stringify({
        query: "mutation Create($file: Upload!, $meta: MetadataInput!) { create(file: $file, meta: $meta) }",
        variables: {
          file: null,
          meta: {
            description: form.prompt || form.name,
            website:     null,
            twitter:     null,
            telegram:    form.tgLink || null,
            buy:         null,
            sell:        null,
            creator:     wallet,
          }
        }
      }));
      uploadForm.append("map", JSON.stringify({ "0": ["variables.file"] }));
      uploadForm.append("0", new File([imageBlob], "image.png", { type: mimeType }));

      const uploadRes = await fetch("https://funcs.flap.sh/api/upload", {
        method: "POST",
        body: uploadForm,
      });
      if (!uploadRes.ok) throw new Error("Failed to upload metadata to Flap IPFS");
      const uploadData = await uploadRes.json();
      const ipfsCid = uploadData?.data?.create;
      if (!ipfsCid) throw new Error("No IPFS CID returned from Flap upload");
      addLog("METADATA PINNED TO IPFS ✓", true);

      // ── Step 4: MetaMask calls newToken on Flap portal ────────────────────────
      addLog("AWAITING METAMASK — DEPLOYING TOKEN ON FLAP");
      const activeWallet = wallets?.[0];
      if (!activeWallet) throw new Error("No wallet connected");
      await activeWallet.switchChain(56);
      const provider = await activeWallet.getEthereumProvider();

      // Encode newToken(string name, string symbol, string meta) via ethers.js
      // meta = IPFS CID from Flap's upload API (required by contract)
      const iface = new ethers.Interface([
        "function newToken(string name, string symbol, string meta) payable returns (address)"
      ]);
      const calldata = iface.encodeFunctionData("newToken", [
        form.name,
        form.ticker,
        ipfsCid,
      ]);

      const raiseWei = parseFloat(form.raiseAmount) > 0
        ? "0x" + BigInt(Math.floor(parseFloat(form.raiseAmount) * 1e18)).toString(16)
        : "0x0";

      const txHash = await provider.request({
        method: "eth_sendTransaction",
        params: [{
          from:  wallet,
          to:    FLAP_PORTAL,
          data:  calldata,
          value: raiseWei,
        }],
      });
      addLog(`TX SUBMITTED: ${txHash.slice(0,10)}... ✓`, true);
      addLog("WAITING FOR BSC CONFIRMATION");

      // ── Step 5: Tell backend the tx hash ─────────────────────────────────────
      const confirmRes = await fetch(`${API_URL}/launch/confirm/${prep.agent_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tx_hash: txHash }),
      });
      if (!confirmRes.ok) {
        const err = await confirmRes.json().catch(() => ({}));
        throw new Error(err.detail || "Confirm failed");
      }
      addLog("BACKEND CONFIRMED — RUNNING PIPELINE");

      // ── Step 6: Poll until active ─────────────────────────────────────────────
      const progressLogs = [
        [2000,  "PARSING TOKEN ADDRESS FROM BSC",         false],
        [5000,  "CREATING AGENT TRADING WALLET",          false],
        [7000,  "ASSIGNING TELEGRAM BOT FROM POOL",       false],
        [9000,  "GENERATING INTRO POSTS VIA CLAUDE",      false],
        [11000, "DISPATCHING POSTS TO CHANNEL",           false],
        [12000, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",  null],
      ];
      let resolved = false;
      progressLogs.forEach(([t, msg, ok]) => {
        setTimeout(() => { if (!resolved) addLog(msg, ok); }, t);
      });

      await new Promise((resolve, reject) => {
        const poll = setInterval(async () => {
          try {
            const r = await fetch(`${API_URL}/agent/${prep.agent_id}`);
            const agent = await r.json();
            if (agent.status === "active") {
              clearInterval(poll);
              resolved = true;
              setLaunchResult(agent);
              addLog("AGENT ONLINE ✓", true);
              setTimeout(() => { setLaunching(false); setLaunched(true); setStep(5); }, 600);
              resolve();
            } else if (agent.status === "error") {
              clearInterval(poll);
              reject(new Error(agent.error_message || "Launch failed"));
            }
          } catch(e) {}
        }, 3000);
        setTimeout(() => { clearInterval(poll); reject(new Error("Launch timed out")); }, 180000);
      });

    } catch(e) {
      addLog(`ERROR: ${e.message}`, false);
      setTimeout(() => setLaunching(false), 1000);
    }
  }

  const M = "'Share Tech Mono',monospace";
  const R = "'Rajdhani',sans-serif";
  const G = "#C9A84C";

  const Label = ({children, mb=7}) => (
    <div style={{fontFamily:M,fontSize:8,color:"#A89868",marginBottom:mb,letterSpacing:3}}>{children}</div>
  );

  const Card = ({children, style={}}) => (
    <div style={{border:"1px solid #E8E0C8",background:"#FEFCF5",...style}}>{children}</div>
  );

  return (
    <div style={{minHeight:"100vh",background:"#F5F2EA",color:"#0A0A0A",fontFamily:M,display:"flex",flexDirection:"column",position:"relative"}}>
      <style>{`
        @import url("https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;600;700&display=swap");
        *{box-sizing:border-box;margin:0;padding:0}
        ::-webkit-scrollbar{width:2px}::-webkit-scrollbar-thumb{background:#C9A84C55}
        .pg{position:fixed;inset:0;pointer-events:none;z-index:0;
          background-image:linear-gradient(rgba(0,0,0,0.06) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,0.06) 1px,transparent 1px);
          background-size:8px 8px}
        .sl{position:fixed;inset:0;pointer-events:none;z-index:0;
          background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,0.012) 3px,rgba(0,0,0,0.012) 4px)}
        .r{position:relative;z-index:1}
        .ctl{position:fixed;top:0;left:0;width:32px;height:32px;border-right:1px solid #C9A84C44;border-bottom:1px solid #C9A84C44;z-index:100;pointer-events:none}
        .ctr{position:fixed;top:0;right:0;width:32px;height:32px;border-left:1px solid #C9A84C44;border-bottom:1px solid #C9A84C44;z-index:100;pointer-events:none}
        .cbl{position:fixed;bottom:0;left:0;width:32px;height:32px;border-right:1px solid #C9A84C44;border-top:1px solid #C9A84C44;z-index:100;pointer-events:none}
        .cbr{position:fixed;bottom:0;right:0;width:32px;height:32px;border-left:1px solid #C9A84C44;border-top:1px solid #C9A84C44;z-index:100;pointer-events:none}
        @keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
        @keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.2}}
        @keyframes gs{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
        @keyframes si{from{opacity:0;transform:translateX(-3px)}to{opacity:1;transform:translateX(0)}}
        .fu{animation:fadeUp .35s cubic-bezier(.16,1,.3,1) forwards}
        .bk{animation:blink 1s step-end infinite}
        .pd{animation:pulse 2s ease infinite}
        .le{animation:si .15s ease forwards}
        .gt{background:linear-gradient(120deg,#B8902A,#E8C060,#C9A84C,#F0D878,#C9A84C);background-size:300% 300%;
          -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;animation:gs 5s ease infinite}
        input,textarea{background:#fff;border:1px solid #DDD8C8;color:#1A1A0A;font-family:'Share Tech Mono',monospace;
          font-size:12px;padding:11px 14px;outline:none;width:100%;transition:border-color .15s,box-shadow .15s;
          border-radius:0;letter-spacing:.04em}
        input:focus,textarea:focus{border-color:#C9A84C;box-shadow:0 0 0 3px rgba(201,168,76,.08)}
        textarea{resize:none;line-height:1.65}
        .ar{transition:all .1s;cursor:pointer;border-left:3px solid transparent!important}
        .ar:hover{border-left-color:#C9A84C55!important;background:#FEFDF5!important}
        .ar.sel{border-left-color:#C9A84C!important}
        .nb{transition:all .12s;cursor:pointer;letter-spacing:.15em}
        .nb:hover{opacity:.85;transform:translateY(-1px)}
        .nb:active{transform:translateY(0)}
        .st{transition:all .15s;cursor:pointer}
        .st:hover{opacity:.8}
        .uz{border:1px dashed #C9A84C88;background:#FEFCF5;padding:24px;text-align:center;cursor:pointer;transition:all .15s}
        .uz:hover{border-color:#C9A84C;background:rgba(201,168,76,.03)}
        .ck{display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:12px 14px;
          border:1px solid #E8E0C8;background:#FEFCF5;transition:border-color .1s}
        .ck:hover{border-color:#C9A84C88}
      `}</style>

      <div className="pg"/><div className="sl"/>
      <div className="ctl"/><div className="ctr"/><div className="cbl"/><div className="cbr"/>

      {/* TOPBAR */}
      <div className="r" style={{height:52,borderBottom:"1px solid #E0D8C0",display:"flex",alignItems:"center",
        justifyContent:"space-between",padding:"0 28px",background:"rgba(245,242,234,0.97)",
        backdropFilter:"blur(8px)",position:"sticky",top:0,zIndex:50,flexShrink:0}}>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <span className="gt" style={{fontFamily:R,fontSize:22,fontWeight:700,letterSpacing:6}}>4GENT</span>
          <div style={{width:1,height:14,background:"#DDD8C8"}}/>
          <span style={{fontFamily:M,fontSize:8,color:"#C0B070",letterSpacing:4}}>AGENT LAUNCHPAD</span>
        </div>
        <div style={{display:"flex",gap:2}}>
          {STEPS.map((s,i)=>(
            <div key={s.id} className="st" onClick={()=>(i<=step||launched)&&setStep(i)} style={{
              display:"flex",alignItems:"center",gap:5,padding:"4px 10px",
              border:`1px solid ${i===step?G:"#E8E0C8"}`,
              background:i===step?"rgba(201,168,76,0.07)":"transparent",
              cursor:(i<=step||launched)?"pointer":"default",
              opacity:i>step&&!launched?0.25:1,
            }}>
              <span style={{fontFamily:M,fontSize:7,color:i===step?G:"#C0B888"}}>{s.num}</span>
              <span style={{fontFamily:M,fontSize:8,color:i===step?"#1A1A0A":"#A89868",letterSpacing:1}}>{s.label}</span>
              {i<step&&<span style={{fontSize:8,color:G}}>◈</span>}
            </div>
          ))}
        </div>
      </div>

      <div className="r" style={{display:"flex",flex:1,overflow:"hidden"}}>

        {/* LEFT SIDEBAR */}
        <div style={{width:200,borderRight:"1px solid #E0D8C0",padding:"22px 16px",display:"flex",
          flexDirection:"column",gap:14,overflowY:"auto",flexShrink:0,background:"#FAF8F2"}}>
          <div style={{fontFamily:M,fontSize:7,color:"#C0B888",letterSpacing:4}}>PREVIEW</div>
          <div style={{width:56,height:56,border:`1px solid ${form.archetype?G:"#DDD8C8"}`,
            display:"flex",alignItems:"center",justifyContent:"center",
            background:form.archetype?"rgba(201,168,76,0.05)":"#F8F5ED",position:"relative",overflow:"hidden"}}>
            {imgPreview
              ? <img src={imgPreview} style={{width:"100%",height:"100%",objectFit:"cover"}}/>
              : <span style={{fontFamily:M,fontSize:22,color:form.archetype?G:"#DDD8C8"}}>{arch?.icon||"○"}</span>
            }
            {launched&&<div className="pd" style={{position:"absolute",bottom:-3,right:-3,width:8,height:8,
              borderRadius:"50%",background:"#5DB870",border:"2px solid #F5F2EA"}}/>}
          </div>
          <div>
            <div style={{fontFamily:R,fontSize:18,fontWeight:700,color:"#1A1A0A",letterSpacing:2,lineHeight:1.1}}>
              {form.name||<span style={{color:"#DDD8C8"}}>NAME</span>}
            </div>
            <div style={{fontFamily:M,fontSize:9,color:"#B8A870",marginTop:3}}>
              ${form.ticker||<span style={{color:"#DDD8C8"}}>TICKER</span>}
            </div>
          </div>
          {arch&&<div style={{padding:"8px 10px",border:"1px solid #E8E0C8",background:"#FEFCF2"}}>
            <div style={{fontFamily:M,fontSize:7,color:G,marginBottom:3,letterSpacing:2}}>{arch.label}</div>
            <div style={{fontFamily:M,fontSize:9,color:"#888068",lineHeight:1.5}}>{arch.desc}</div>
          </div>}
          {form.tgLink&&step>=2&&<div style={{padding:"8px 10px",border:"1px solid #E8E0C8"}}>
            <div style={{fontFamily:M,fontSize:7,color:"#C0B888",marginBottom:4,letterSpacing:2}}>CHANNEL</div>
            <div style={{fontFamily:M,fontSize:8,color:"#A89868"}}>t.me/{handle}</div>
            {launched&&<div style={{fontFamily:M,fontSize:7,color:"#5DB870",marginTop:3}}>● LIVE</div>}
          </div>}
          {wallet&&<div style={{padding:"8px 10px",border:"1px solid #E8D898",background:"#FDF8E8"}}>
            <div style={{fontFamily:M,fontSize:7,color:"#B09030",marginBottom:3,letterSpacing:2}}>WALLET</div>
            <div style={{fontFamily:M,fontSize:8,color:"#808030"}}>{wallet.slice(0,6)}...{wallet.slice(-4)}</div>
          </div>}
          {form.trading&&step>=3&&<div style={{padding:"8px 10px",border:"1px solid #E8C8A0"}}>
            <div style={{fontFamily:M,fontSize:7,color:"#B07830",marginBottom:3,letterSpacing:2}}>AUTO TRADE</div>
            <div style={{fontFamily:M,fontSize:8,color:"#888068",lineHeight:1.7}}>
              {form.maxTrade} BNB MAX<br/>{form.dailyLimit} BNB/DAY<br/>SL {form.stopLoss}%
            </div>
          </div>}
        </div>

        {/* MAIN CONTENT */}
        <div style={{flex:1,overflowY:"auto",padding:"40px 44px",background:"transparent"}}>

          {/* 01 IDENTITY */}
          {step===0&&<div className="fu">
            <div style={{marginBottom:6}}>
              <span style={{fontFamily:R,fontSize:44,fontWeight:700,color:"#1A1A0A",letterSpacing:2}}>WHO IS YOUR </span>
              <span className="gt" style={{fontFamily:R,fontSize:44,fontWeight:700,letterSpacing:2}}>AGENT</span>
              <span style={{fontFamily:R,fontSize:44,fontWeight:700,color:"#1A1A0A",letterSpacing:2}}>?</span>
            </div>
            <div style={{fontFamily:M,fontSize:10,color:"#A89868",marginBottom:36,letterSpacing:2}}>LAUNCH AN AUTONOMOUS AI AGENT ON FLAP.SH IN 60 SECONDS.</div>
            <div style={{maxWidth:500,display:"flex",flexDirection:"column",gap:22}}>
              <div><Label>AGENT NAME</Label><input placeholder="DegenDave / CoffeeBot / AlphaMaxx..." value={form.name} onChange={e=>upd("name",e.target.value)}/></div>
              <div><Label>TICKER</Label><input placeholder="DAVE / COFF / ALPHA" value={form.ticker} onChange={e=>upd("ticker",e.target.value.toUpperCase())}/></div>
              <div>
                <Label>ARCHETYPE</Label>
                <div style={{display:"flex",flexDirection:"column",gap:4}}>
                  {ARCHETYPES.map((a,i)=>(
                    <div key={a.id} className={`ar${form.archetype===a.id?" sel":""}`}
                      onClick={()=>upd("archetype",a.id)} style={{
                        padding:"11px 14px",display:"flex",alignItems:"center",gap:14,
                        border:`1px solid ${form.archetype===a.id?G:"#E8E0C8"}`,
                        background:form.archetype===a.id?"rgba(201,168,76,0.06)":"#FEFCF5",
                        ...(i===5?{borderTop:"1px dashed #E8E0C8",marginTop:4}:{}),
                      }}>
                      <span style={{fontFamily:M,fontSize:16,color:form.archetype===a.id?G:"#C8C0A0",width:20,textAlign:"center"}}>{a.icon}</span>
                      <div style={{flex:1}}>
                        <div style={{fontFamily:M,fontSize:10,color:form.archetype===a.id?"#1A1A0A":"#706850",letterSpacing:2}}>{a.label}</div>
                        <div style={{fontFamily:M,fontSize:9,color:"#A89868",marginTop:2}}>{a.desc}</div>
                      </div>
                      {form.archetype===a.id&&<span style={{color:G,fontSize:10,marginLeft:"auto"}}>◈</span>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>}

          {/* 02 BRAIN */}
          {step===1&&<div className="fu">
            <div style={{marginBottom:6}}>
              <span className="gt" style={{fontFamily:R,fontSize:44,fontWeight:700,letterSpacing:2}}>WHAT</span>
              <span style={{fontFamily:R,fontSize:44,fontWeight:700,color:"#1A1A0A",letterSpacing:2}}> DOES IT DO?</span>
            </div>
            <div style={{fontFamily:M,fontSize:10,color:"#A89868",marginBottom:36,letterSpacing:2}}>
              {form.archetype==="custom"?"YOUR AGENT. YOUR RULES. WRITE ANYTHING.":"THIS PROMPT IS YOUR AGENT'S EDGE. BE SPECIFIC."}
            </div>
            <div style={{maxWidth:520,display:"flex",flexDirection:"column",gap:24}}>
              <div>
                <Label>AGENT MISSION</Label>
                <textarea rows={5} placeholder={
                  form.archetype==="custom"?"define your agent completely — what it does, how it thinks, who it's for..."
                  :form.archetype==="degen"?"e.g. ape anything under 10 BNB raise with 3+ whale wallets in first 5 mins..."
                  :"describe exactly what your agent watches for and what it calls..."
                } value={form.prompt} onChange={e=>upd("prompt",e.target.value)}/>
                <div style={{fontFamily:M,fontSize:8,color:"#C8C0A0",marginTop:5}}>{form.prompt.length} CHARS</div>
              </div>
              <div>
                <Label>TOKEN IMAGE</Label>
                <div className="uz" onClick={()=>fileRef.current?.click()}>
                  {imgPreview
                    ? <img src={imgPreview} style={{maxHeight:80,maxWidth:"100%",objectFit:"contain"}}/>
                    : <>
                        <div style={{fontFamily:M,fontSize:24,color:G,marginBottom:8}}>◈</div>
                        <div style={{fontFamily:M,fontSize:9,color:"#A89868",letterSpacing:2}}>CLICK TO UPLOAD</div>
                        <div style={{fontFamily:M,fontSize:8,color:"#C0B888",marginTop:4}}>PNG / JPG — SQUARE RECOMMENDED</div>
                      </>
                  }
                </div>
                <input ref={fileRef} type="file" accept="image/*" style={{display:"none"}} onChange={handleFile}/>
                {imgFile&&<div style={{fontFamily:M,fontSize:8,color:"#5DB870",marginTop:5}}>◈ {imgFile.name}</div>}
              </div>
            </div>
          </div>}

          {/* 03 TELEGRAM */}
          {step===2&&<div className="fu">
            <div style={{marginBottom:6}}>
              <span style={{fontFamily:R,fontSize:44,fontWeight:700,color:"#1A1A0A",letterSpacing:2}}>SET UP YOUR </span>
              <span className="gt" style={{fontFamily:R,fontSize:44,fontWeight:700,letterSpacing:2}}>CHANNEL</span>
            </div>
            <div style={{fontFamily:M,fontSize:10,color:"#A89868",marginBottom:36,letterSpacing:2}}>YOU CREATE IT. WE RUN IT. TAKES 2 MINUTES.</div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:20,maxWidth:720}}>
              <div style={{display:"flex",flexDirection:"column",gap:8}}>
                <Label mb={10}>SETUP INSTRUCTIONS</Label>
                {TG_STEPS.map((txt,i)=>(
                  <div key={i} style={{display:"flex",gap:12,padding:"10px 14px",border:"1px solid #E8E0C8",background:"#FEFCF5"}}>
                    <div style={{fontFamily:M,fontSize:11,color:G,fontWeight:700,flexShrink:0,paddingTop:1}}>{i+1}</div>
                    <div style={{fontFamily:M,fontSize:9,color:"#605848",lineHeight:1.6}}>{txt}</div>
                  </div>
                ))}
                <div style={{marginTop:6}}>
                  <Label>YOUR CHANNEL LINK</Label>
                  <input placeholder="t.me/yourchannel" value={form.tgLink} onChange={e=>upd("tgLink",e.target.value)}/>
                  {form.tgLink.length>5&&(
                    <div style={{display:"flex",alignItems:"center",gap:8,marginTop:5}}>
                      <div onClick={async()=>{
                        setVerifyStatus("checking");
                        try {
                          const r = await fetch(`${API_URL}/verify-channel`,{
                            method:"POST", headers:{"Content-Type":"application/json"},
                            body: JSON.stringify({channel_link: form.tgLink}),
                          });
                          const d = await r.json();
                          setVerifyStatus(d.verified ? "ok" : "fail");
                        } catch(e) { setVerifyStatus("fail"); }
                      }} style={{fontFamily:M,fontSize:8,color:"#C0A840",cursor:"pointer",letterSpacing:2,
                        padding:"4px 8px",border:"1px solid #C0A84044",background:"#FDF8E811"}}>
                        {verifyStatus==="checking"?"CHECKING...":verifyStatus==="ok"?"✓ CHANNEL FOUND":verifyStatus==="fail"?"✗ NOT FOUND — CHECK LINK":"◈ VERIFY CHANNEL"}
                      </div>
                    </div>
                  )}
                </div>
              </div>
              <div style={{display:"flex",flexDirection:"column",gap:8}}>
                <Label mb={10}>FIRST POSTS PREVIEW</Label>
                {posts.length>0 ? posts.map((p,i)=>(
                  <div key={i} onClick={()=>setPreviewIdx(i)} style={{
                    padding:"11px 13px",cursor:"pointer",
                    border:`1px solid ${previewIdx===i?G:"#E8E0C8"}`,
                    background:previewIdx===i?"rgba(201,168,76,0.05)":"#FEFCF5",
                  }}>
                    <div style={{display:"flex",gap:8,marginBottom:5}}>
                      <div style={{fontFamily:M,fontSize:7,color:G,border:"1px solid #E8D898",padding:"1px 5px"}}>{p.type}</div>
                    </div>
                    <div style={{fontFamily:M,fontSize:9,color:"#605848",lineHeight:1.6}}>{p.text}</div>
                  </div>
                )) : (
                  <Card style={{padding:"24px",textAlign:"center"}}>
                    <div style={{fontFamily:M,fontSize:9,color:"#C8C0A0"}}>COMPLETE STEPS 01 + 02 FIRST</div>
                  </Card>
                )}
              </div>
            </div>
          </div>}

          {/* 04 TRADING */}
          {step===3&&<div className="fu">
            <div style={{marginBottom:6}}>
              <span className="gt" style={{fontFamily:R,fontSize:44,fontWeight:700,letterSpacing:2}}>AUTONOMOUS</span>
              <span style={{fontFamily:R,fontSize:44,fontWeight:700,color:"#1A1A0A",letterSpacing:2}}> TRADING</span>
            </div>
            <div style={{fontFamily:M,fontSize:10,color:"#A89868",marginBottom:36,letterSpacing:2}}>OPTIONAL. SKIP IF YOU JUST WANT THE SOCIAL LAYER.</div>
            <div style={{maxWidth:460,display:"flex",flexDirection:"column",gap:12}}>
              <div onClick={()=>upd("trading",!form.trading)} style={{
                padding:"14px 16px",cursor:"pointer",
                border:`1px solid ${form.trading?"#C08040":"#E8E0C8"}`,
                background:form.trading?"rgba(192,128,64,0.06)":"#FEFCF5",
                display:"flex",alignItems:"center",justifyContent:"space-between",
              }}>
                <div>
                  <div style={{fontFamily:M,fontSize:10,color:form.trading?"#804020":"#706850",letterSpacing:2}}>ENABLE AUTONOMOUS TRADING</div>
                  <div style={{fontFamily:M,fontSize:8,color:"#C0B888",marginTop:3}}>AGENT EXECUTES TRADES ON FLAP</div>
                </div>
                <div style={{width:30,height:17,borderRadius:9,background:form.trading?"#C08040":"#E0D8C0",position:"relative",flexShrink:0,transition:"background 0.2s"}}>
                  <div style={{position:"absolute",top:2,left:form.trading?13:2,width:13,height:13,borderRadius:"50%",background:"#fff",transition:"left 0.2s",boxShadow:"0 1px 2px rgba(0,0,0,.12)"}}/>
                </div>
              </div>
              {form.trading&&<div className="fu" style={{display:"flex",flexDirection:"column",gap:10}}>
                {[["MAX TRADE SIZE (BNB)","maxTrade","0.1"],["DAILY SPEND LIMIT (BNB)","dailyLimit","1.0"],["STOP LOSS (%)","stopLoss","50"]].map(([l,k,p])=>(
                  <div key={k}><Label>{l}</Label><input type="number" value={form[k]} onChange={e=>upd(k,e.target.value)} placeholder={p}/></div>
                ))}
              </div>}
              {!form.trading&&<Card style={{padding:"14px"}}>
                <div style={{fontFamily:M,fontSize:9,color:"#A89868",lineHeight:1.9}}>
                  WITHOUT TRADING:<br/>
                  <span style={{color:"#C0B888"}}>→ AGENT POSTS CALLS AND RESEARCH<br/>→ HOLDERS TRADE MANUALLY<br/>→ ZERO WALLET RISK.</span>
                </div>
              </Card>}
            </div>
          </div>}

          {/* 05 LAUNCH */}
          {step===4&&<div className="fu">
            <div style={{marginBottom:6}}>
              <span style={{fontFamily:R,fontSize:44,fontWeight:700,color:"#1A1A0A",letterSpacing:2}}>READY TO </span>
              <span className="gt" style={{fontFamily:R,fontSize:44,fontWeight:700,letterSpacing:2}}>LAUNCH</span>
            </div>
            <div style={{fontFamily:M,fontSize:10,color:"#A89868",marginBottom:32,letterSpacing:2}}>CONNECT WALLET AND DEPLOY. ~$0.50 IN GAS.</div>
            <div style={{maxWidth:520,display:"flex",flexDirection:"column",gap:18}}>

              <div>
                <Label>01 — CONNECT YOUR BSC WALLET</Label>
                {!ready
                  ? <div style={{padding:"13px",border:"1px solid #E0D8C8",fontFamily:M,fontSize:9,color:"#A89868"}}>LOADING...</div>
                  : !wallet
                  ? <button onClick={connectWallet} className="nb" style={{
                      width:"100%",padding:"13px",background:"#FEFCF5",
                      border:`1px solid ${G}`,color:G,fontFamily:M,fontSize:11,letterSpacing:4,
                    }}>◈ CONNECT WALLET</button>
                  : <div style={{padding:"11px 14px",border:"1px solid #5DB870",background:"rgba(93,184,112,0.05)",display:"flex",alignItems:"center",gap:10}}>
                      <div className="pd" style={{width:7,height:7,borderRadius:"50%",background:"#5DB870",flexShrink:0}}/>
                      <div style={{fontFamily:M,fontSize:9,color:"#408050"}}>{wallet.slice(0,6)}...{wallet.slice(-4)}</div>
                    </div>
                }
                <div style={{fontFamily:M,fontSize:7,color:"#A89868",marginTop:5,lineHeight:1.6}}>
                  YOUR WALLET IS SET AS THE BENEFICIARY — YOU CLAIM REV SHARE FEES AFTER GRADUATION.
                </div>
              </div>

              <div>
                <Label>02 — REVIEW CONFIG</Label>
                <Card>
                  {[
                    ["AGENT",    `${form.name||"—"} / $${form.ticker||"—"}`],
                    ["ARCHETYPE",arch?.label||"—"],
                    ["CHANNEL",  `t.me/${handle}`],
                    ["IMAGE",    imgFile ? imgFile.name : "NONE"],
                    ["TRADING",  form.trading?`ON — ${form.maxTrade} BNB MAX / ${form.dailyLimit} BNB DAILY`:"OFF"],
                    ["SEED BUY", parseFloat(form.raiseAmount) > 0 ? `${form.raiseAmount} BNB AT DEPLOY` : "NONE"],
                    ["PLATFORM", "FLAP.SH — BSC"],
                  ].map(([k,v])=>(
                    <div key={k} style={{display:"flex",padding:"8px 14px",borderBottom:"1px solid #F0E8D8",gap:16}}>
                      <div style={{fontFamily:M,fontSize:7,color:"#C0B888",width:100,flexShrink:0}}>{k}</div>
                      <div style={{fontFamily:M,fontSize:9,color:"#504838"}}>{v}</div>
                    </div>
                  ))}
                </Card>
              </div>

              <div>
                <Label>03 — INITIAL BUY (OPTIONAL)</Label>
                <div style={{border:"1px solid #E8E0C8",background:"#FEFCF5",padding:"14px"}}>
                  <div style={{fontFamily:M,fontSize:8,color:"#A89868",marginBottom:10,lineHeight:1.7}}>
                    BUY YOUR OWN TOKEN AT DEPLOY. AMOUNT IN BNB.
                  </div>
                  <div style={{display:"flex",alignItems:"center",gap:10}}>
                    <input type="number" min="0" max="20" step="0.01"
                      value={form.raiseAmount} onChange={e=>upd("raiseAmount",e.target.value)}
                      style={{width:120,fontFamily:M,fontSize:12}}
                      placeholder="0.00"/>
                    <div style={{fontFamily:M,fontSize:10,color:"#706850",letterSpacing:2}}>BNB</div>
                  </div>
                </div>
              </div>

              <div>
                <Label>04 — ACKNOWLEDGE</Label>
                <div className="ck" onClick={()=>setFeeAck(!feeAck)}>
                  <div style={{width:14,height:14,border:`1px solid ${G}`,flexShrink:0,marginTop:1,
                    display:"flex",alignItems:"center",justifyContent:"center",
                    background:feeAck?G:"transparent"}}>
                    {feeAck&&<span style={{color:"#fff",fontSize:10,fontWeight:700,lineHeight:1}}>✓</span>}
                  </div>
                  <div style={{fontFamily:M,fontSize:9,color:"#605848",lineHeight:1.7}}>
                    I UNDERSTAND LAUNCHING COSTS ~$0.50 IN GAS PAID FROM MY WALLET.<br/>
                    <span style={{color:"#A89868"}}>FLAP.SH REV SHARE ACTIVATES AFTER GRADUATION TO PANCAKESWAP V3.</span>
                  </div>
                </div>
              </div>

              {!launching&&!launched&&(
                <button onClick={()=>canNext()&&startLaunch()} disabled={!canNext()} className="nb" style={{
                  background:canNext()?"#0A0A0A":"#F0EAE0",
                  border:`1px solid ${canNext()?G:"#E0D8C8"}`,
                  color:canNext()?G:"#C8C0A0",
                  fontFamily:M,fontSize:12,padding:"15px",width:"100%",letterSpacing:5,
                }}>◈ DEPLOY AGENT</button>
              )}

              {launching&&(
                <div ref={logsRef} style={{border:"1px solid #1A1A0A",background:"#0A0A08",padding:"16px",maxHeight:220,overflowY:"auto"}}>
                  {logs.map((l,i)=>(
                    <div key={i} className="le" style={{
                      fontFamily:M,fontSize:10,lineHeight:1.9,display:"flex",gap:10,alignItems:"center",
                      color:l.ok===true?G:l.ok===null?"#2A2A1A":"#484838",
                    }}>
                      <span style={{flexShrink:0,color:l.ok===true?G:"#2A2A1A"}}>{l.ok===true?"◈":l.ok===null?"—":"·"}</span>
                      {l.msg}
                    </div>
                  ))}
                  {launching&&<span className="bk" style={{color:G,fontFamily:M}}>▌</span>}
                </div>
              )}
            </div>
          </div>}

          {/* 06 LIVE */}
          {step===5&&launched&&<div className="fu">
            <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:6}}>
              <div className="pd" style={{width:8,height:8,borderRadius:"50%",background:"#5DB870"}}/>
              <span className="gt" style={{fontFamily:R,fontSize:44,fontWeight:700,letterSpacing:2}}>AGENT ONLINE</span>
            </div>
            <div style={{fontFamily:M,fontSize:10,color:"#A89868",marginBottom:28,letterSpacing:2}}>
              {(form.name||"AGENT").toUpperCase()} IS LIVE ON FLAP.SH. WATCHING. POSTING. NEVER SLEEPING.
            </div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,maxWidth:760}}>

              {launchResult?.bot_username && (
                <Card style={{gridColumn:"1/-1",padding:"14px 18px",background:"#F5FDF7",border:"1px solid #B8E4C0"}}>
                  <div style={{fontFamily:M,fontSize:9,color:"#308050",letterSpacing:2,marginBottom:10}}>◈ ONE LAST STEP — ACTIVATE YOUR AGENT</div>
                  <div style={{fontFamily:M,fontSize:9,color:"#405848",lineHeight:2}}>
                    Add your assigned bot to your Telegram channel as admin:<br/>
                    <span style={{color:"#308050",fontWeight:700}}>
                      1. Open channel → Administrators → Add Admin<br/>
                      2. Search <span style={{letterSpacing:1}}>{launchResult.bot_username}</span><br/>
                      3. Grant Post Messages → Done
                    </span><br/>
                    <span style={{color:"#90B8A0"}}>Agent starts posting automatically within 10 minutes.</span>
                  </div>
                </Card>
              )}

              <Card style={{display:"flex",flexDirection:"column"}}>
                <div style={{padding:"10px 14px",borderBottom:"1px solid #F0E8D8"}}>
                  <div style={{fontFamily:M,fontSize:9,color:"#A89868",letterSpacing:2}}>ACTIVATE OWNER CONTROLS</div>
                </div>
                <div style={{padding:"16px 14px",display:"flex",flexDirection:"column",gap:14}}>
                  <div style={{padding:"14px",border:"1px solid #E8D898",background:"#FDF8E8"}}>
                    <div style={{fontFamily:M,fontSize:7,color:"#B09030",marginBottom:8,letterSpacing:2}}>YOUR CLAIM CODE</div>
                    <div style={{fontFamily:R,fontSize:32,fontWeight:700,color:"#1A1A0A",letterSpacing:10}}>{launchResult?.claim_code || "LOADING..."}</div>
                    <div style={{fontFamily:M,fontSize:8,color:"#C0A840",marginTop:8,lineHeight:1.7}}>
                      DM THIS CODE TO @4GentBot<br/>TO UNLOCK OWNER COMMANDS.<br/>
                      <span style={{color:"#D0C080"}}>EXPIRES IN 24 HOURS.</span>
                    </div>
                  </div>
                  <div style={{fontFamily:M,fontSize:9,color:"#A89868",lineHeight:2}}>
                    AFTER CLAIMING:<br/>
                    <span style={{color:"#C0B070"}}>/stats /fees /pause /resume</span>
                  </div>
                </div>
              </Card>

              <div style={{gridColumn:"1/-1",display:"grid",gridTemplateColumns:"repeat(4,1fr)",border:"1px solid #E8E0C8",background:"#FEFCF5"}}>
                {[
                  ["TOKEN",       `$${form.ticker}`, launchResult?.token_address ? launchResult.token_address.slice(0,6)+"..."+launchResult.token_address.slice(-4) : "0x..."],
                  ["PLATFORM",    "FLAP.SH",         "BSC MAINNET"],
                  ["HOLDERS",     "1",               "GROWING"],
                  ["REV SHARE",   "0 BNB",           "ACTIVATES AT GRAD"],
                ].map(([l,v,s],i)=>(
                  <div key={i} style={{padding:"14px 18px",borderRight:i<3?"1px solid #F0E8D8":"none"}}>
                    <div style={{fontFamily:M,fontSize:7,color:"#C0B888",marginBottom:5,letterSpacing:2}}>{l}</div>
                    <div className="gt" style={{fontFamily:R,fontSize:26,fontWeight:700,lineHeight:1}}>{v}</div>
                    <div style={{fontFamily:M,fontSize:7,color:"#C8C0A0",marginTop:4}}>{s}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>}

        </div>

        {/* RIGHT NAV */}
        <div style={{width:148,borderLeft:"1px solid #E0D8C0",padding:"20px 14px",display:"flex",
          flexDirection:"column",justifyContent:"space-between",flexShrink:0,background:"#FAF8F2"}}>
          <div style={{display:"flex",flexDirection:"column",gap:3}}>
            <div style={{fontFamily:M,fontSize:7,color:"#C8C0A0",marginBottom:8,letterSpacing:4}}>NAVIGATE</div>
            {STEPS.map((s,i)=>(
              <div key={s.id} onClick={()=>(i<=step||launched)&&setStep(i)} style={{
                padding:"7px 10px",
                border:`1px solid ${i===step?G:"#EAE4D4"}`,
                borderLeft:i===step?`3px solid ${G}`:"1px solid #EAE4D4",
                background:i===step?"rgba(201,168,76,0.06)":"transparent",
                cursor:(i<=step||launched)?"pointer":"default",
                opacity:i>step&&!launched?0.18:1,
                display:"flex",alignItems:"center",gap:7,transition:"all 0.1s",
              }}>
                <span style={{fontFamily:M,fontSize:7,color:"#C0B888"}}>{s.num}</span>
                <span style={{fontFamily:M,fontSize:8,color:i===step?"#1A1A0A":"#A89868",letterSpacing:1}}>{s.label}</span>
                {i<step&&<span style={{color:G,fontSize:8,marginLeft:"auto"}}>◈</span>}
              </div>
            ))}
          </div>
          <div style={{display:"flex",flexDirection:"column",gap:6}}>
            {step<4&&(
              <button onClick={()=>canNext()&&setStep(s=>s+1)} disabled={!canNext()} className="nb" style={{
                background:canNext()?"#0A0A0A":"#F0EAE0",
                border:`1px solid ${canNext()?G:"#E0D8C8"}`,
                color:canNext()?G:"#C8C0A0",
                fontFamily:M,fontSize:10,padding:"9px",width:"100%",letterSpacing:3,
              }}>NEXT ▸</button>
            )}
            {step>0&&step<5&&(
              <button onClick={()=>setStep(s=>s-1)} style={{
                background:"transparent",border:"1px solid #E0D8C8",color:"#A89868",
                fontFamily:M,fontSize:9,padding:"7px",cursor:"pointer",letterSpacing:2,
              }}>◂ BACK</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function FourGent() {
  return (
    <PrivyProvider
      appId="cmmcxq2zq009h0dl5jt94qh8t"
      config={{
        appearance: { theme: "dark", accentColor: "#C9A84C" },
        defaultChain: {
          id: 56, name: "BNB Smart Chain", network: "bsc",
          nativeCurrency: { name: "BNB", symbol: "BNB", decimals: 18 },
          rpcUrls: { default: { http: ["https://bsc-dataseed1.binance.org/"] } },
          blockExplorers: { default: { name: "BscScan", url: "https://bscscan.com" } },
        },
        supportedChains: [{
          id: 56, name: "BNB Smart Chain", network: "bsc",
          nativeCurrency: { name: "BNB", symbol: "BNB", decimals: 18 },
          rpcUrls: { default: { http: ["https://bsc-dataseed1.binance.org/"] } },
          blockExplorers: { default: { name: "BscScan", url: "https://bscscan.com" } },
        }],
      }}
    >
      <App />
    </PrivyProvider>
  );
}
