import ctypes
from pathlib import Path
from tree_sitter import Language, Parser
lib=ctypes.cdll.LoadLibrary("spantree/cobol_ent.so"); fn=lib.tree_sitter_cobol; fn.restype=ctypes.c_void_p
P=Parser(Language(fn()))
def dump(n,src,d=0,maxd=3):
    t=src[n.start_byte:n.end_byte].decode('utf8','replace').strip().replace("\n"," ")[:66]
    print("  "*d+f"{n.type} «{t}»")
    if d<maxd:
        for c in n.children: dump(c,src,d+1,maxd)
src=Path("carddemo/cbl/COACTUPC.cbl").read_bytes(); tree=P.parse(src)
shown=set(); st=[tree.root_node]
while st:
    n=st.pop()
    txt=src[n.start_byte:n.end_byte][:80]
    if n.type=="move_statement" and b"TO CDEMO-TO-PROGRAM" in txt.upper() and "mv" not in shown:
        shown.add("mv"); print("="*66); dump(n,src)
    if n.type=="data_description" and b"VALUE" in txt.upper() and b"LIT-" in txt.upper() and "dd" not in shown:
        shown.add("dd"); print("="*66); dump(n,src)
    st.extend(reversed(n.children))
