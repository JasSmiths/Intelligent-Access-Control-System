import {expect, test, type Page} from "playwright/test";

const previous="a".repeat(64), current="b".repeat(64);
type Payload={name:string;actions:Array<{target_ids:string[];apprise_revision?:string}>;confirmation_token?:string};

async function fixtures(page:Page) {
  const unexpected:string[]=[], writes:Payload[]=[], confirmations:Array<{action:string;payload:Payload}>=[];
  const rule={id:"saved-rule",name:"Saved B",trigger_event:"vehicle.entry",conditions:[],is_active:true,
    actions:[{id:"saved-action",type:"mobile",target_mode:"selected",target_ids:["apprise:1"],
      apprise_revision:previous,title_template:"Synthetic alert",message_template:"Private message for B"}]};
  let accepted:Payload|null=null;
  const catalog={triggers:[{id:"vehicles",label:"Vehicles",events:[{value:"vehicle.entry",label:"Vehicle entered",description:"Synthetic event"}]}],
    variables:[],actionable_notifications:[],gate_malfunction_stages:[],mock_context:{},
    integrations:[{id:"mobile",name:"Mobile",provider:"Apprise",configured:true,apprise_revision:current,
      endpoints:[{id:"apprise:0",provider:"Apprise",label:"Current B",detail:"Synthetic destination B",apprise_revision:current},
        {id:"apprise:1",provider:"Apprise",label:"Current C",detail:"Synthetic destination C",apprise_revision:current}]}]};
  await page.route("**/api/v1/**",async route=>{
    const request=route.request(), path=new URL(request.url()).pathname;
    if(request.method()==="POST" && path==="/api/v1/action-confirmations") {
      confirmations.push(request.postDataJSON());await route.fulfill({json:{confirmation_token:"inert"}});return;
    }
    if(request.method()==="PATCH" && path==="/api/v1/notifications/rules/saved-rule") {
      const payload=request.postDataJSON() as Payload;writes.push(payload);
      if(payload.actions.some(action=>action.apprise_revision!==current)) {
        await route.fulfill({status:409,json:{detail:"Recipient catalog changed; replace the action and review recipients."}});return;
      }
      accepted=payload;await route.fulfill({json:{...rule,...payload}});return;
    }
    if(request.method()!=="GET") {unexpected.push(`${request.method()} ${path}`);await route.abort("blockedbyclient");return;}
    let body:unknown;
    switch(path) {
      case "/api/v1/auth/status":body={setup_required:false,authenticated:true,user:{id:"fixture-admin",first_name:"Alex",last_name:"Operator",full_name:"Alex Operator",role:"admin",is_active:true,preferences:{sidebarCollapsed:false}}};break;
      case "/api/v1/settings":case "/api/v1/alerts":case "/api/v1/schedules":body=[];break;
      case "/api/v1/maintenance/status":body={is_active:false};break;
      case "/api/v1/integrations/gate/status":body={configured:false,connected:false};break;
      case "/api/v1/notifications/catalog":body=catalog;break;
      case "/api/v1/notifications/rules":body=[{...rule,...accepted}];break;
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
  return {unexpected,writes,confirmations};
}

for(const viewport of [{width:1440,height:900},{width:390,height:844}]) {
  test(`stale selected recipient stays held through unrelated edit at ${viewport.width}`,async({page},testInfo)=>{
    await page.setViewportSize(viewport);const fixture=await fixtures(page);
    await page.goto("/settings/notifications");await page.getByRole("button",{name:"Saved B",exact:true}).click();
    const editor=page.getByRole("dialog");
    await expect(editor.getByText(/The destination list changed/)).toBeVisible();
    await expect(editor.getByText("Current C",{exact:true})).toHaveCount(0);
    await editor.getByLabel("Workflow name").fill("Retained draft");
    await editor.getByRole("button",{name:"Save",exact:true}).click();
    await expect(page.getByText(/Recipient catalog changed/)).toBeVisible();
    await expect(editor.getByLabel("Workflow name")).toHaveValue("Retained draft");
    expect(fixture.writes).toHaveLength(1);expect(fixture.writes[0].actions[0].apprise_revision).toBe(previous);
    expect(fixture.confirmations[0].action).toBe("notification_rule.update");
    expect(fixture.confirmations[0].payload.actions[0].apprise_revision).toBe(previous);
    expect(fixture.unexpected).toEqual([]);
    await page.screenshot({path:testInfo.outputPath("stale-recipient-held.png"),fullPage:true});
  });

  test(`deliberate replacement binds B from current catalog at ${viewport.width}`,async({page},testInfo)=>{
    await page.setViewportSize(viewport);const fixture=await fixtures(page);
    await page.goto("/settings/notifications");await page.getByRole("button",{name:"Saved B",exact:true}).click();
    const editor=page.getByRole("dialog");
    await editor.getByRole("button",{name:"Remove action"}).click();
    await editor.getByRole("button",{name:"Add Action",exact:true}).click();
    await editor.getByRole("button",{name:/Mobile/}).click();
    await editor.locator(".action-method-grid button").filter({hasText:"Current B"}).click();
    await editor.getByRole("button",{name:"Confirm Selection"}).click();
    await expect(editor.getByText("Current B",{exact:true})).toBeVisible();
    await expect(editor.getByText(/The destination list changed/)).toHaveCount(0);
    await page.screenshot({path:testInfo.outputPath("recipient-deliberately-reselected.png"),fullPage:true});
    await editor.getByRole("button",{name:"Save",exact:true}).click();await expect(editor).toHaveCount(0);
    expect(fixture.writes).toHaveLength(1);expect(fixture.writes[0].actions[0].target_ids).toEqual(["apprise:0"]);
    expect(fixture.writes[0].actions[0].apprise_revision).toBe(current);
    expect(fixture.confirmations[0].payload.actions[0].apprise_revision).toBe(current);
    expect(fixture.unexpected).toEqual([]);
  });
}
