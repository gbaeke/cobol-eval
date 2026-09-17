import ctypes
from pathlib import Path
from tree_sitter import Language, Parser
lib=ctypes.cdll.LoadLibrary("spantree/cobol_ent.so"); fn=lib.tree_sitter_cobol; fn.restype=ctypes.c_void_p
P=Parser(Language(fn()))
def dump(n,src,d=0,maxd=3):
    t=src[n.start_byte:n.end_byte].decode('utf8','replace').strip().replace("\n"," ")[:72]
    fields=[]
    for i,c in enumerate(n.children):
        fn_=n.field_name_for_child(i)
        if fn_: fields.append(f"{fn_}={c.type}")
    print("  "*d + f"{n.type}{' ['+' '.join(fields)+']' if fields else ''} «{t}»")
    if d<maxd:
        for c in n.children: dump(c,src,d+1,maxd)
WANT=["paragraph","perform_statement","call_statement","copy_statement","exec_cics_statement","select_statement","program_id_paragraph"]
src=Path("carddemo/cbl/COBIL00C.cbl").read_bytes(); tree=P.parse(src)
shown={}
st=[tree.root_node]
while st:
    n=st.pop()
    if n.type in WANT and shown.get(n.type,0)<1:
        shown[n.type]=1; print("="*72); dump(n,src,0, 2 if n.type=="paragraph" else 3)
    st.extend(reversed(n.children))
src2=Path("carddemo/cbl/CBTRN02C.cbl").read_bytes(); tree2=P.parse(src2)
st=[tree2.root_node]
shown2={}
while st:
    n=st.pop()
    if n.type in ("call_statement","select_statement") and shown2.get(n.type,0)<1:
        shown2[n.type]=1; print("="*72); dump(n,src2)
    st.extend(reversed(n.children))
