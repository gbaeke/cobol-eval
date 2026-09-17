import ctypes
from pathlib import Path
from tree_sitter import Language, Parser

def load(so, sym):
    lib = ctypes.cdll.LoadLibrary(so)
    fn = getattr(lib, sym); fn.restype = ctypes.c_void_p
    return Parser(Language(fn()))

def errpct(P, src):
    tree = P.parse(src)
    errb = 0; st = [tree.root_node]
    while st:
        n = st.pop()
        if n.type == "ERROR":
            errb += n.end_byte - n.start_byte; continue
        st.extend(n.children)
    return 100 * errb / max(len(src), 1)

A = load("cobol/cobol.so", "tree_sitter_COBOL")
B = load("spantree/cobol_ent.so", "tree_sitter_cobol")

print(f"{'file':16}{'bytes':>7}{'yutaro%':>9}{'spantree%':>11}   kind")
agg = {}
for f in sorted(Path('carddemo/cbl').glob('*')) + sorted(Path('carddemo/cpy').glob('*')):
    src = f.read_bytes()
    a = errpct(A, src); b = errpct(B, src)
    kind = "cpy" if f.suffix.lower() == ".cpy" else ("CICS" if b"EXEC CICS" in src.upper() else "batch")
    agg.setdefault(kind, []).append((a, b))
    print(f"{f.name:16}{len(src):7}{a:9.1f}{b:11.1f}   {kind}")
print()
print(f"{'group':8}{'files':>6}{'yutaro mean%':>14}{'yutaro clean':>14}{'spantree mean%':>16}{'spantree clean':>16}")
for k, v in agg.items():
    ym = sum(x[0] for x in v)/len(v); sm = sum(x[1] for x in v)/len(v)
    yc = sum(1 for x in v if x[0] < 1); sc = sum(1 for x in v if x[1] < 1)
    print(f"{k:8}{len(v):6}{ym:14.1f}{str(yc)+'/'+str(len(v)):>14}{sm:16.1f}{str(sc)+'/'+str(len(v)):>16}")
