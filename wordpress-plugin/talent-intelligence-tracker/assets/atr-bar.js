/* Phone sign-up bar on the hiring-tracker pages (includes/atr_bar.php). */
(function(){
function init(){
  var k='tit_atr_bar_closed',bar=document.getElementById('tit-atr-bar');
  if(!bar)return;
  var closed=false;
  try{closed=window.localStorage.getItem(k)==='1';}catch(e){}
  if(closed)return;
  bar.hidden=false;document.body.classList.add('tit-atr-bar-on');
  document.getElementById('tit-atr-bar-close').addEventListener('click',function(){
    bar.hidden=true;document.body.classList.remove('tit-atr-bar-on');
    try{window.localStorage.setItem(k,'1');}catch(e){}
  });
}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',init);}else{init();}
})();
