const playwrightModule=process.env.PLAYWRIGHT_MODULE||"playwright";
const {chromium}=await import(playwrightModule);

const benchmarkUrl=process.argv[2];
const runs=Math.max(1,Number(process.argv[3])||1);
if(!benchmarkUrl){
  throw new Error("Usage: node tests/benchmark_static_replay_multiview.mjs <benchmark-url> [runs]");
}

function median(values){
  const sorted=values.slice().sort((a,b)=>a-b);
  return sorted[Math.floor(sorted.length/2)];
}

const browser=await chromium.launch({headless:true});
try{
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  const browserErrors=[];
  page.on("console",message=>{
    if(message.type()==="error")browserErrors.push(message.text());
  });
  page.on("pageerror",error=>browserErrors.push(error.message));

  const samples=[];
  for(let run=0;run<runs;run++){
    const url=new URL(benchmarkUrl);
    url.searchParams.set("benchmark_run",String(run));
    await page.goto(url.toString(),{waitUntil:"domcontentloaded"});
    await page.locator("#result[data-complete=true]").waitFor({
      state:"attached",
      timeout:90000
    });
    const sample=JSON.parse(await page.locator("#result").textContent());
    if(sample.error)throw new Error(JSON.stringify(sample));
    if(sample.advancing_viewers!==sample.viewers){
      throw new Error("Not every replay advanced: "+JSON.stringify(sample));
    }
    samples.push(sample);
  }

  if(browserErrors.length){
    throw new Error("Browser errors: "+browserErrors.join(" | "));
  }
  const summary={
    runs,
    median_raf_fps:median(samples.map(sample=>sample.raf_fps)),
    median_p95_gap_ms:median(samples.map(sample=>sample.p95_gap_ms)),
    median_gaps_over_50ms:median(samples.map(sample=>sample.gaps_over_50ms)),
    samples
  };
  console.log(JSON.stringify(summary));
}finally{
  await browser.close();
}
