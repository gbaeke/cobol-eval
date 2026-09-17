from setuptools import Extension, setup
setup(
    name="tree-sitter-cobol-enterprise",
    version="0.1.0",
    packages=["tree_sitter_cobol"],
    ext_modules=[Extension(
        "tree_sitter_cobol._binding",
        sources=["binding.c", "src/parser.c", "src/scanner.c"],
        include_dirs=["src"],
        extra_compile_args=["-O1", "-std=c11"],
        define_macros=[("PY_SSIZE_T_CLEAN", None)],
    )],
    zip_safe=False,
)
