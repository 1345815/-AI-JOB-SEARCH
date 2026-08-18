"""Extract recruitment form fields from pasted HTML without extra dependencies."""
import hashlib, json
from html.parser import HTMLParser
from pathlib import Path
LIBRARY=Path(__file__).with_name("form_field_templates.json")
class Parser(HTMLParser):
 def __init__(self): super().__init__(); self.labels={}; self.controls=[]; self.current=""; self.parts=[]
 def handle_starttag(self,t,a):
  d=dict(a)
  if t=="label": self.current=d.get("for",""); self.parts=[]
  if t in ("input","select","textarea"): self.controls.append({"id":d.get("id",""),"name":d.get("name","") or d.get("id", ""),"type":d.get("type","select" if t=="select" else "textarea" if t=="textarea" else "text"),"required":"required" in d,"value":d.get("value","")})
 def handle_data(self,d): self.parts.append(d.strip())
 def handle_endtag(self,t):
  if t=="label": self.labels[self.current]=" ".join(x for x in self.parts if x)
def extract_form(html, source_url=""):
 p=Parser(); p.feed(html); mapping=json.loads(LIBRARY.read_text(encoding="utf-8"))["field_mappings"]; result={}
 for c in p.controls:
  label=p.labels.get(c["id"]) or p.labels.get(c["name"]) or c["name"]; key=next((v for k,v in mapping.items() if k in label),""); item=result.setdefault(c["name"] or c["id"],{"key":key,"label":label,"type":c["type"],"required":c["required"],"options":[],"selector":"#"+c["id"] if c["id"] else "[name='"+c["name"]+"']"})
  if c["type"] in ("radio","checkbox") and c["value"]: item["options"].append(c["value"])
 return {"form_id":hashlib.sha256((source_url+html[:1000]).encode()).hexdigest()[:16],"source_url":source_url,"fields":list(result.values())}
