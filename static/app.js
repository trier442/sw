function setupAnswerDrafts(){
  const form=document.querySelector('[data-answer-form]');
  if(!form)return;
  const key=`draft:${location.pathname}`;
  const radios=[...form.querySelectorAll('input[type=radio]')];
  const saved=JSON.parse(localStorage.getItem(key)||'{}');
  radios.forEach((radio,index)=>{
    const group=Math.floor(index/5);
    if(!radios.some((r,i)=>Math.floor(i/5)===group&&r.checked)&&saved[group]){
      const candidate=radios.find((r,i)=>Math.floor(i/5)===group&&r.value===String(saved[group]));
      if(candidate)candidate.checked=true;
    }
    radio.addEventListener('change',()=>{
      const data={};
      radios.forEach((r,i)=>{if(r.checked)data[Math.floor(i/5)]=r.value});
      localStorage.setItem(key,JSON.stringify(data));
    });
  });
  form.addEventListener('submit',()=>localStorage.removeItem(key));
}

async function setupReportExport(filename){
  const save=document.getElementById('save-report');
  const share=document.getElementById('share-report');
  const report=document.getElementById('score-report');
  const text=document.getElementById('share-text');
  if(!save||!report)return;
  const makeBlob=async()=>{
    if(!window.html2canvas)throw new Error('이미지 모듈을 불러오지 못했습니다.');
    const canvas=await html2canvas(report,{scale:2,backgroundColor:'#ffffff',useCORS:true});
    return new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
  };
  save.addEventListener('click',async()=>{
    try{const blob=await makeBlob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.download=filename;a.href=url;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(err){alert(err.message)}
  });
  if(share)share.addEventListener('click',async()=>{
    try{
      const blob=await makeBlob();const file=new File([blob],filename,{type:'image/png'});
      if(navigator.canShare&&navigator.canShare({files:[file]})){await navigator.share({title:'더소피 매일학습 성적표',text:text?.value||'',files:[file]})}
      else if(navigator.share){await navigator.share({title:'더소피 매일학습 성적표',text:text?.value||''})}
      else{await navigator.clipboard.writeText(text?.value||'');alert('안내 문구를 복사했습니다. PNG 저장 후 원하는 앱에 첨부해 주세요.')}
    }catch(err){if(err.name!=='AbortError')alert(err.message)}
  });
}
window.setupReportExport=setupReportExport;
window.addEventListener('DOMContentLoaded',setupAnswerDrafts);
