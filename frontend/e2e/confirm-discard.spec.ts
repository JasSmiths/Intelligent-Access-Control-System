import {expect, test, type Page} from "playwright/test";

// Regression: after the first in-app confirmation, the shared confirm host stayed in <body>, the next
// editor marked it inert, and the following prompt's buttons ignored real clicks.
async function fixtures(page:Page) {
  const unexpected:string[]=[];
  const rule={id:"saved-rule",name:"Saved B",trigger_event:"vehicle.entry",conditions:[],is_active:true,actions:[]};
  const catalog={triggers:[{id:"vehicles",label:"Vehicles",events:[{value:"vehicle.entry",label:"Vehicle entered",description:"Synthetic event"}]}],
    variables:[],actionable_notifications:[],gate_malfunction_stages:[],mock_context:{},integrations:[]};
  await page.route("**/api/v1/**",async route=>{
    const request=route.request(), path=new URL(request.url()).pathname;
    if(request.method()!=="GET") {unexpected.push(`${request.method()} ${path}`);await route.abort("blockedbyclient");return;}
    let body:unknown;
    switch(path) {
      case "/api/v1/auth/status":body={setup_required:false,authenticated:true,user:{id:"fixture-admin",first_name:"Alex",last_name:"Operator",full_name:"Alex Operator",role:"admin",is_active:true,preferences:{sidebarCollapsed:false}}};break;
      case "/api/v1/settings":case "/api/v1/alerts":case "/api/v1/schedules":body=[];break;
      case "/api/v1/maintenance/status":body={is_active:false};break;
      case "/api/v1/integrations/gate/status":body={configured:false,connected:false};break;
      case "/api/v1/notifications/catalog":body=catalog;break;
      case "/api/v1/notifications/rules":body=[rule];break;
      case "/api/v1/notifications/runs":case "/api/v1/people":body={items:[],next_cursor:null};break;
      case "/api/v1/integrations/unifi-protect/cameras":body={cameras:[]};break;
      default:unexpected.push(`GET ${path}`);await route.abort("blockedbyclient");return;
    }
    await route.fulfill({json:body});
  });
  await page.routeWebSocket("**/*",socket=>socket.onMessage(raw=>{
    const message=JSON.parse(String(raw));
    if(message.type==="client.ping")socket.send(JSON.stringify({type:"connection.pong",payload:{id:message.payload.id},created_at:"2026-10-14T12:00:00Z"}));
  }));
  return {unexpected};
}

test("notification editor closes from Discard on repeated prompts in one session",async({page})=>{
  const fixture=await fixtures(page);let nativePrompts=0;page.on("dialog",async dialog=>{nativePrompts+=1;await dialog.dismiss();});
  await page.goto("/settings/notifications");
  const discard=page.getByRole("alertdialog",{name:"Discard unsaved notification changes?"});
  const openDirty=async()=>{
    await page.getByRole("button",{name:"Saved B",exact:true}).click();
    const editor=page.getByRole("dialog");await editor.getByLabel("Workflow name").fill("Changed");return editor;
  };
  for(let round=1;round<=3;round+=1) {
    const editor=await openDirty();
    if(round===2) {
      await editor.getByRole("button",{name:"Close notification editor"}).click();
      await discard.getByRole("button",{name:"Keep editing"}).click();await expect(discard).toHaveCount(0);
      await page.keyboard.press("Escape");await expect(discard).toBeVisible();
      await page.keyboard.press("Escape");await expect(discard).toHaveCount(0);
      await expect(editor.getByLabel("Workflow name")).toHaveValue("Changed");
    }
    await editor.getByRole("button",{name:"Close notification editor"}).click();
    await expect(discard).toBeVisible();
    expect(await discard.evaluate(element=>Boolean(element.closest("[inert]")))).toBe(false);
    await discard.getByRole("button",{name:"Discard"}).click();
    await expect(discard).toHaveCount(0);await expect(editor).toHaveCount(0);
    await expect(page.locator("[data-confirm-host]")).toHaveCount(0);
  }
  expect(nativePrompts).toBe(0);expect(fixture.unexpected).toEqual([]);
});
