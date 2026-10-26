# Vendored legacy sources (reference only)

Verbatim copies of the original OpenJigWare files that the compatibility tests
need. They are never edited; `.gitattributes` stores them byte for byte, and the
golden data pins their SHA-256.

| File | Upstream | SHA-256 | Used by |
|---|---|---|---|
| `OpenJigWare/COjw_25_Genibo.cs` | [ojw5014/OpenJigWare](https://github.com/ojw5014/OpenJigWare) `OpenJigWare/COjw_25_Genibo.cs`, commit `086be9be0d73` (2021-08-18; identical on `master` as of 2026-10) | `f8063c54ff20c0b5b9cb16ac8a159c95e27df5cd054cb74c452d22b593843e0a` | `compat/csharp-reference` compiles it to record golden bytes; `tests/test_golden_csharp.py`, `tests/test_reference_harness.py` |
| `Examples/Ex20_Genibo/Form1.cs` | [ojw5014/Examples](https://github.com/ojw5014/Examples) `1/Ex20_Genibo/WindowsFormsApplication1/WindowsFormsApplication1/Form1.cs` | `4b3f7e053c2fcae47a54593d8f8629e9f01f1e4ca48ac4635a410c803ce63fd5` | `tests/test_legacy_shim.py` (migration scanner), `examples/ex20_genibo` |

Neither upstream repository declares a license. These files are included
unmodified, with attribution, for interoperability testing. Check the terms with
the upstream author before redistributing this repository.

The rest of the original OpenJigWare toolkit, the C# examples and the prebuilt
DLLs are **not** part of this repository. `compat/compare_binaries.py` (which
checks those DLLs) needs a separate checkout of them.
