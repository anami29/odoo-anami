#!/usr/bin/env python3
"""Static validation of the addon: model/field/method/action coherence.

Reasoning about whether a view field exists is how FIX-003 and FIX-004 got
shipped. This reads the actual files.
"""
import ast
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIELD_CALLS = {
    "Char", "Text", "Html", "Boolean", "Integer", "Float", "Monetary",
    "Date", "Datetime", "Binary", "Image", "Selection", "Many2one",
    "One2many", "Many2many", "Reference", "Json", "Properties",
}

models = {}            # _name -> {"fields": set, "methods": set, "inherit": [], "file": path}
errors = []
warnings = []


def collect(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        name = inherit = None
        inherits = []
        fields, methods = set(), set()
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and stmt.targets and \
                    isinstance(stmt.targets[0], ast.Name):
                t = stmt.targets[0].id
                if t == "_name" and isinstance(stmt.value, ast.Constant):
                    name = stmt.value.value
                elif t == "_inherit":
                    if isinstance(stmt.value, ast.Constant):
                        inherits = [stmt.value.value]
                    elif isinstance(stmt.value, (ast.List, ast.Tuple)):
                        inherits = [e.value for e in stmt.value.elts
                                    if isinstance(e, ast.Constant)]
                elif isinstance(stmt.value, ast.Call):
                    f = stmt.value.func
                    if isinstance(f, ast.Attribute) and f.attr in FIELD_CALLS \
                            and isinstance(f.value, ast.Name) \
                            and f.value.id == "fields":
                        fields.add(t)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.add(stmt.name)
        key = name or (inherits[0] if inherits else None)
        if not key:
            continue
        entry = models.setdefault(
            key, {"fields": set(), "methods": set(), "inherit": [],
                  "file": path, "declared": False})
        entry["fields"] |= fields
        entry["methods"] |= methods
        entry["inherit"] += inherits
        if name:
            entry["declared"] = True
            entry["file"] = path


for sub in ("models", "wizards", "controllers"):
    d = os.path.join(ROOT, sub)
    if not os.path.isdir(d):
        continue
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".py"):
            collect(os.path.join(d, fn))

# Framework-supplied fields present on every model, plus mixins we inherit.
BUILTIN = {
    "id", "display_name", "create_date", "create_uid", "write_date",
    "write_uid", "__last_update", "sequence", "active",
    # mail.thread / mail.activity.mixin
    "message_ids", "message_follower_ids", "message_partner_ids",
    "activity_ids", "activity_state", "activity_user_id", "activity_type_id",
    "activity_date_deadline", "activity_summary", "message_needaction",
    "message_attachment_count", "rating_ids", "website_message_ids",
    "message_has_error", "message_is_follower", "message_main_attachment_id",
}


def model_fields(m):
    """Fields of a model including anything it _inherits by mixin."""
    seen, out = set(), set(BUILTIN)
    stack = [m]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in models:
            continue
        seen.add(cur)
        out |= models[cur]["fields"]
        stack += models[cur]["inherit"]
    return out


def model_methods(m):
    seen, out = set(), set()
    stack = [m]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in models:
            continue
        seen.add(cur)
        out |= models[cur]["methods"]
        stack += models[cur]["inherit"]
    return out


# ---------------------------------------------------------------- ACL csv
acl = os.path.join(ROOT, "security", "ir.model.access.csv")
rows = [r for r in open(acl, encoding="utf-8").read().splitlines() if r.strip()]
header = rows[0].split(",")
if len(header) != 8:
    errors.append("ACL header has %d columns, expected 8" % len(header))
seen_ids = set()
declared_models = {k for k, v in models.items() if v["declared"]}
acl_models = set()
for i, row in enumerate(rows[1:], start=2):
    parts = row.split(",")
    if len(parts) != 8:
        errors.append("ACL line %d has %d columns" % (i, len(parts)))
        continue
    if parts[0] in seen_ids:
        errors.append("ACL duplicate id %s at line %d" % (parts[0], i))
    seen_ids.add(parts[0])
    ref = parts[2]
    if not ref.startswith("model_"):
        errors.append("ACL line %d: bad model ref %s" % (i, ref))
        continue
    dotted = ref[len("model_"):].replace("_", ".")
    acl_models.add(dotted)

# An underscore in a model name collides with the dot substitution, so match
# by normalising both sides instead of round-tripping.
norm = {m.replace(".", "_"): m for m in declared_models}
for i, row in enumerate(rows[1:], start=2):
    ref = row.split(",")[2]
    key = ref[len("model_"):]
    if key not in norm:
        errors.append("ACL line %d references unknown model: %s" % (i, ref))

covered = {norm[r.split(",")[2][6:]] for r in rows[1:]
           if r.split(",")[2][6:] in norm}
TRANSIENT_OK = set()
for m in sorted(declared_models - covered):
    warnings.append("model has NO ACL row: %s" % m)

# ------------------------------------------------------------- XML views
xml_ids = set()
actions = set()
view_nodes = []
for dirpath, _dirs, files in os.walk(ROOT):
    for fn in sorted(files):
        if not fn.endswith(".xml"):
            continue
        path = os.path.join(dirpath, fn)
        root = ET.parse(path).getroot()
        for rec in root.iter("record"):
            rid = rec.get("id")
            if rid:
                xml_ids.add(rid)
                if rec.get("model") == "ir.actions.act_window":
                    actions.add(rid)
            if rec.get("model") == "ir.ui.view":
                mnode = rec.find("field[@name='model']")
                arch = rec.find("field[@name='arch']")
                if mnode is not None and arch is not None:
                    view_nodes.append((path, rid, mnode.text.strip(), arch))
        for mi in root.iter("menuitem"):
            if mi.get("id"):
                xml_ids.add(mi.get("id"))

# menu actions resolve
for dirpath, _dirs, files in os.walk(ROOT):
    for fn in sorted(files):
        if not fn.endswith(".xml"):
            continue
        path = os.path.join(dirpath, fn)
        for mi in ET.parse(path).getroot().iter("menuitem"):
            act = mi.get("action")
            if act and "." not in act and act not in actions:
                errors.append("%s: menu %s -> unknown action %s"
                              % (os.path.basename(path), mi.get("id"), act))

# fields and buttons inside arch
SUBMODELS = {
    # one2many / many2many field -> comodel, so nested lists are checked
    # against the right model.
}
for m, meta in models.items():
    pass


def comodel_of(model, field):
    """Comodel of a relational field, read back out of the source."""
    if model not in models:
        return None
    src = open(models[model]["file"], encoding="utf-8").read()
    pat = re.compile(
        r"^\s*%s\s*=\s*fields\.(One2many|Many2many|Many2one)\(\s*[\"']([^\"']+)[\"']"
        % re.escape(field), re.M)
    mt = pat.search(src)
    if mt:
        return mt.group(2)
    # related=... inherits the comodel of the target; skip, too deep.
    return None


def walk_arch(node, model, path, vid, depth=0):
    for child in node:
        tag = child.tag
        if tag == "field":
            fname = child.get("name")
            if fname and fname not in model_fields(model):
                # A model this addon only EXTENDS carries core fields the
                # parser cannot see, so an unknown name there is
                # unverifiable rather than wrong. Odoo source is not in
                # this container to grep, so it is reported, not passed.
                if models.get(model, {}).get("declared"):
                    errors.append("%s [%s]: field '%s' not on %s"
                                  % (os.path.basename(path), vid, fname,
                                     model))
                else:
                    warnings.append(
                        "%s [%s]: '%s' on extended model %s — core field, "
                        "not verifiable here"
                        % (os.path.basename(path), vid, fname, model))
            sub = comodel_of(model, fname) if fname else None
            walk_arch(child, sub or model, path, vid, depth + 1)
        elif tag == "button":
            btype = child.get("type")
            bname = child.get("name")
            if btype == "object" and bname and \
                    bname not in model_methods(model):
                errors.append("%s [%s]: button '%s' not a method on %s"
                              % (os.path.basename(path), vid, bname, model))
            elif btype == "action" and bname and "." not in (bname or "") \
                    and bname not in actions:
                errors.append("%s [%s]: button action %s unknown"
                              % (os.path.basename(path), vid, bname))
        else:
            walk_arch(child, model, path, vid, depth + 1)


for path, vid, model, arch in view_nodes:
    if model not in models:
        warnings.append("%s [%s]: view on model not in this addon: %s"
                        % (os.path.basename(path), vid, model))
        continue
    walk_arch(arch, model, path, vid)

# ------------------------------------------------- manifest data coverage
manifest = open(os.path.join(ROOT, "__manifest__.py"), encoding="utf-8").read()
listed = re.findall(r"[\"']((?:security|data|views|wizards|report)/[^\"']+)[\"']",
                    manifest)
for entry in listed:
    if not os.path.exists(os.path.join(ROOT, entry)):
        errors.append("manifest lists missing file: %s" % entry)
on_disk = []
for sub in ("security", "data", "views", "wizards"):
    d = os.path.join(ROOT, sub)
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith((".xml", ".csv")):
                on_disk.append("%s/%s" % (sub, fn))
for entry in on_disk:
    if entry not in listed:
        warnings.append("file on disk but NOT in manifest: %s" % entry)

# ------------------------------------------------------- model __init__
init = open(os.path.join(ROOT, "models", "__init__.py"), encoding="utf-8").read()
for fn in sorted(os.listdir(os.path.join(ROOT, "models"))):
    if fn.endswith(".py") and fn != "__init__.py":
        if ("from . import %s" % fn[:-3]) not in init:
            errors.append("models/%s not imported in __init__.py" % fn)

# ------------------------------------------------------------ XML comment
for dirpath, _dirs, files in os.walk(ROOT):
    for fn in files:
        if fn.endswith(".xml"):
            txt = open(os.path.join(dirpath, fn), encoding="utf-8").read()
            for cm in re.findall(r"<!--(.*?)-->", txt, re.S):
                if "--" in cm:
                    errors.append("%s: '--' inside an XML comment" % fn)

# --------------------------------------------------------------- report
print("models declared: %d" % len(declared_models))
print("views checked:   %d" % len(view_nodes))
print("acl rows:        %d" % (len(rows) - 1))
print()
for w in warnings:
    print("WARN  %s" % w)
print()
for e in errors:
    print("ERROR %s" % e)
print()
print("%d error(s), %d warning(s)" % (len(errors), len(warnings)))
sys.exit(1 if errors else 0)
