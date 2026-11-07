/* Both backgrounds are derived from one hue. Brand/title colours are independent. */
window.KnowledgePalette = hex => {
  const rgb=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255),max=Math.max(...rgb),min=Math.min(...rgb),delta=max-min,l=(max+min)/2;
  let h=0,s=0;if(delta){s=delta/(1-Math.abs(2*l-1));h=(max===rgb[0]?(rgb[1]-rgb[2])/delta%6:max===rgb[1]?(rgb[2]-rgb[0])/delta+2:(rgb[0]-rgb[1])/delta+4)/6;}if(h<0)h+=1;
  const color=(sat,light)=>{const c=(1-Math.abs(2*light-1))*sat,x=c*(1-Math.abs((h*6)%2-1)),m=light-c/2,k=Math.floor(h*6)%6,v=[[c,x,0],[x,c,0],[0,c,x],[0,x,c],[x,0,c],[c,0,x]][k];return '#'+v.map(t=>Math.round((t+m)*255).toString(16).padStart(2,'0')).join('')};
  return {accent:hex,background:color(Math.min(.6,s*.85),.18),background_bottom:color(Math.min(.6,s*.85),.12),paper:color(Math.min(.45,s*.75),.925),ink:color(Math.min(.3,s),.23),secondary:color(Math.min(.3,s*.45),.58),highlight:'#ECC76F'};
};
