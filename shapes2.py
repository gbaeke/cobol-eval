import ctypes
from pathlib import Path
from collections import Counter
from tree_sitter import Language, Parser
lib=ctypes.cdll.LoadLibrary("spantree/cobol_ent.so"); fn=lib.tree_sitter_cobol; fn.restype=ctypes.c_void_p
P=Parser(Language(fn()))
def dump(n,src,d=0,maxd=2):
    t=src[n.start_byte:n.end_byte].decode('utf8','replace').strip().replace("\n"," ")[:70]
    print("  "*d+f"{n.type} «{t}»")
    if d<maxd:
        for c in n.children: dump(c,src,d+1,maxd)
want={"cics_xctl","cics_link","cics_read","cics_send"}
kinds=Counter()
shown=set()
for f in sorted(Path("carddemo/cbl").glob("*")):
    src=f.read_bytes(); st=[P.parse(src).root_node]
    while st:
        n=st.pop()
        if n.type.startswith("cics_") and n.type!="cics_option": kinds[n.type]+=1
        if n.type in want and n.type not in shown:
            shown.add(n.type); print("="*70, f.name); dump(n,src)
        st.extend(reversed(n.children))
print("\n=== all CICS command node types across corpus:")
for k,v in kinds.most_common(): print(f"  {v:4}  {k}")
