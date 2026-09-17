import ctypes
from collections import Counter
from pathlib import Path
from tree_sitter import Language, Parser
lib = ctypes.cdll.LoadLibrary("spantree/cobol_ent.so"); fn = lib.tree_sitter_cobol; fn.restype = ctypes.c_void_p
P = Parser(Language(fn()))
KEYS = ("program","paragraph","section","call","perform","copy","exec","cics","sql","goback","goto","select","file","identifier","literal")
for name in ("carddemo/cbl/COBIL00C.cbl", "carddemo/cbl/CBTRN02C.cbl"):
    src = Path(name).read_bytes(); tree = P.parse(src)
    c = Counter(); st=[tree.root_node]
    while st:
        n = st.pop(); c[n.type]+=1; st.extend(n.children)
    print("="*70); print(name, "root:", tree.root_node.type)
    for k,v in sorted(c.items(), key=lambda kv:-kv[1]):
        if any(s in k.lower() for s in KEYS) and v>0 and not k.startswith('"'):
            print(f"   {v:5}  {k}")
