import re
SENSITIVE={"id_card","emergency_contact_phone"}
def build_fill_plan(form_id,fields,profile):
 out=[]
 for f in fields:
  key=f.get("key",""); value=None if key in SENSITIVE else profile.get(key); typ=f.get("type", "text"); strategy="radio" if typ=="radio" else "select" if typ=="select" else "date_normalize" if typ=="date" else "direct"
  if strategy=="date_normalize" and value: value=re.sub(r"年|/|\\.","-",str(value)).replace("月","-").replace("日","").strip("-")
  out.append({"label":f.get("label",""),"key":key,"selector":f.get("selector",""),"value":value,"strategy":strategy,"manual_confirmation":key in SENSITIVE})
 return {"form_id":form_id,"mappings":out}
