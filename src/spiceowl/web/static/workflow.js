/* Public, display-only workflow. No fetch, storage, credentials or live controls. */
// Approved publication copies: exact text is verified against the fixed public assets at build/test time.
export const PUBLICATION_SOURCES = Object.freeze([{"id":"bootstrap-dut","kind":"public_circuit","label":"Public RS9_L5_9bit_Bootstrap circuit with embedded @RNL geometry","path":"data/sources/bootstrap-dut.publication.scs","originalSourceSha256":"9b879b2fb1c6918606317875f3bfc1370f2c6380b34c747c013a3cc9ff5f78b6","publicationSha256":"808da690214b9e8ee1c0b2ce05c4dbf7eda69d6d71a198776d3cd5c53597b2c0","transformation":"Added two publication notice comment lines; the source body is unchanged.","submitted":false,"sourceRepositoryPath":"lib/pipetisar-real/RS9_L5_9bit_Bootstrap/schematic/RS9_L5_9bit_Bootstrap.scs","filename":"bootstrap-dut.publication.scs","text":"// PUBLICATION EXCERPT — explanatory copy, not a submitted simulator input.\n// Original source identity and this derived copy have separate hashes in source-manifest.json.\n// RNL cell 2026_PIPETISAR_RESEARCH/RS9_L5_9bit_Bootstrap\n// @RNL(type=\"header\") {\n//   @VERSION(\"0.3\");\n//   @USE-PACKAGE(name=\"rnl-std-spectre\", version=\"0.3\", path=\"rnl-std-spectre.rnl\");\n//   @USE-PACKAGE(name=\"eda-symbol\", version=\"0.1\", path=\"eda-symbol.rnl\");\n//   @USE-PACKAGE(name=\"eda-style\", version=\"0.1\", path=\"eda-style.rnl\")\n// }\nsubckt RS9_L5_9bit_Bootstrap CLK VBST VDD VIN VSS\n    M9 (CLKN CLK VSS VSS) nch_ulvt_mac l=30n w=250.0n multi=1 nf=1 sd=100n \\\n        ad=1.875e-14 as=1.875e-14 pd=650.0n ps=650.0n nrd=1.338763 \\\n        nrs=1.338763 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M6 (net2 VBST VIN VSS) nch_ulvt_mac l=30n w=2u multi=1 nf=4 sd=100n \\\n        ad=1e-13 as=1.25e-13 pd=2.4u ps=3.5u nrd=0.248269 nrs=0.248269 \\\n        sa=179.203n sb=179.203n sa1=118.069n sa2=174.76n sa3=310.76n \\\n        sa4=171.966n sb1=118.069n sb2=174.76n sb3=310.76n spa=100n \\\n        spa1=100n spa2=100n spa3=100n sap=120.952n sapb=140.537n \\\n        spba=115.715n spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M5 (net3 VBST net2 VSS) nch_ulvt_mac l=30n w=250.0n multi=1 nf=1 \\\n        sd=100n ad=1.875e-14 as=1.875e-14 pd=650.0n ps=650.0n nrd=1.338763 \\\n        nrs=1.338763 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M1 (net3 CLK net2 VSS) nch_ulvt_mac l=30n w=250.0n multi=1 nf=1 \\\n        sd=100n ad=1.875e-14 as=1.875e-14 pd=650.0n ps=650.0n nrd=1.338763 \\\n        nrs=1.338763 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M2 (net2 CLKN VSS VSS) nch_ulvt_mac l=30n w=500n multi=1 nf=1 sd=100n \\\n        ad=3.75e-14 as=3.75e-14 pd=1.15u ps=1.15u nrd=0.662935 \\\n        nrs=0.662935 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M10 (CLKN CLK VDD VDD) pch_ulvt_mac l=30n w=250.0n multi=1 nf=1 \\\n        sd=100n ad=1.875e-14 as=1.875e-14 pd=650.0n ps=650.0n nrd=0.777777 \\\n        nrs=0.777777 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M3 (VDD VBST net1 net1) pch_ulvt_mac l=30n w=500n multi=1 nf=1 sd=100n \\\n        ad=3.75e-14 as=3.75e-14 pd=1.15u ps=1.15u nrd=0.383209 \\\n        nrs=0.383209 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M0 (net3 CLK VDD VDD) pch_ulvt_mac l=30n w=250.0n multi=1 nf=1 sd=100n \\\n        ad=1.875e-14 as=1.875e-14 pd=650.0n ps=650.0n nrd=0.777777 \\\n        nrs=0.777777 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M4 (VBST net3 net1 net1) pch_ulvt_mac l=30n w=500n multi=1 nf=1 \\\n        sd=100n ad=3.75e-14 as=3.75e-14 pd=1.15u ps=1.15u nrd=0.383209 \\\n        nrs=0.383209 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    C1 (net1 net2) cfmom_2t nr=60 lr=3.2u w=50n s=50n stm=2 spm=7 multi=1\n    M7 (net7 CLKN VSS VSS) nch_lvt_mac l=30n w=1u multi=1 nf=2 sd=100n \\\n        ad=5e-14 as=7.5e-14 pd=1.2u ps=2.3u nrd=0.425847 nrs=0.425847 \\\n        sa=112.742n sb=112.742n sa1=93.6047n sa2=112.078n sa3=163.715n \\\n        sa4=110.059n sb1=93.6047n sb2=112.078n sb3=163.715n spa=100n \\\n        spa1=100n spa2=100n spa3=100n sap=104.359n sapb=125.813n \\\n        spba=115.715n spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M8 (VBST VDD net7 VSS) nch_lvt_mac l=30n w=1u multi=1 nf=2 sd=100n \\\n        ad=5e-14 as=7.5e-14 pd=1.2u ps=2.3u nrd=0.425847 nrs=0.425847 \\\n        sa=112.742n sb=112.742n sa1=93.6047n sa2=112.078n sa3=163.715n \\\n        sa4=110.059n sb1=93.6047n sb2=112.078n sb3=163.715n spa=100n \\\n        spa1=100n spa2=100n spa3=100n sap=104.359n sapb=125.813n \\\n        spba=115.715n spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n// @RNL(author=\"rnl-virtuoso\") {\n//   %block_symbol = @DEFINE-SYMBOL(box=[0, -10, 28, 9]) {\n//     @LINE {(0, 0) (4, 0)};\n//     @LINE {(28, 0) (24, 0)};\n//     @LINE {(13, 9) (13, 5)};\n//     @LINE {(15, 9) (15, 5)};\n//     @LINE {(13, -10) (13, -6)};\n//     @RECTANGLE {(4, -6) (24, 5)};\n//     ? @TEXT(\"${name}\", size=1, halign=\"start\", valign=\"middle\", key=\"name\") => @PLACE(24, 5);\n//     ? @TEXT(\"${model}\", size=1, halign=\"middle\", valign=\"middle\", key=\"model\") => @PLACE(14, -0.5);\n//     (0, 0) @PIN(terminal=\"VIN\");\n//     (4.6, 0) @TEXT(\"VIN\", size=1, halign=\"start\", valign=\"middle\");\n//     (28, 0) @PIN(terminal=\"VBST\");\n//     (23.4, 0) @TEXT(\"VBST\", size=1, halign=\"end\", valign=\"middle\");\n//     (13, 9) @PIN(terminal=\"VDD\");\n//     (13, 4.4) @TEXT(\"VDD\", size=1, halign=\"end\", valign=\"middle\", rotate=90);\n//     (15, 9) @PIN(terminal=\"CLK\");\n//     (15, 4.4) @TEXT(\"CLK\", size=1, halign=\"end\", valign=\"middle\", rotate=90);\n//     (13, -10) @PIN(terminal=\"VSS\");\n//     (13, -5.4) @TEXT(\"VSS\", size=1, halign=\"start\", valign=\"middle\", rotate=90)\n//   };\n//   # @SET-SYMBOL %block_symbol;\n//   %symbol_0 = @DEFINE-SYMBOL(box=[-0.1, -2.8, 3.8, 2.8]) {\n//     @LINE {(2, 1.5) (4, 1.5) (4, 3)};\n//     @LINE {(2, -1.5) (2, 1.5)};\n//     @LINE {(3, -1) (4, -1.5) (3, -2)};\n//     @LINE {(0, 0) (1.5, 0)};\n//     @LINE {(1.5, 1.5) (1.5, -1.5)};\n//     @LINE {(2, -1.5) (4, -1.5) (4, -3)};\n//     ? @TEXT(\"${name}\", size=0.8, halign=\"start\", valign=\"baseline\", key=\"name\") => @PLACE(5, 3.5);\n//     ? @TEXT(\"${model}\", size=0.8, halign=\"end\", valign=\"baseline\", key=\"model\") => @PLACE(3.2, 3.2);\n//     (4, -1) @PIN(terminal=4);\n//     (4, -3) @PIN(terminal=3);\n//     (4, 3) @PIN(terminal=1);\n//     (0, 0) @PIN(terminal=2)\n//   };\n//   #\"M1\" @SET-SYMBOL %symbol_0;\n//   #\"M1\" @PLACE(-24, -6) => @ORIENT(\"R0\");\n//   %symbol_1 = @DEFINE-SYMBOL(box=[-0.1, -2.8, 3.8, 2.8]) {\n//     @LINE {(0, 0) (0.7, 0)};\n//     @LINE {(1.5, 1.5) (1.5, -1.5)};\n//     @LINE {(2, -1.5) (4, -1.5) (4, -3)};\n//     @LINE {(2, 1.5) (4, 1.5) (4, 3)};\n//     (1.1, 0) @CIRCLE(radius=0.4);\n//     @LINE {(3.7, 2) (2.7, 1.5) (3.7, 1)};\n//     @LINE {(2, -1.5) (2, 1.5)};\n//     ? @TEXT(\"${name}\", size=0.8, halign=\"start\", valign=\"baseline\", key=\"name\") => @PLACE(4.9, 3.4);\n//     ? @TEXT(\"${model}\", size=0.8, halign=\"end\", valign=\"baseline\", key=\"model\") => @PLACE(3.3, 3.2);\n//     (4, 3) @PIN(terminal=3);\n//     (4, 1) @PIN(terminal=4);\n//     (0, 0) @PIN(terminal=2);\n//     (4, -3) @PIN(terminal=1)\n//   };\n//   #\"M3\" @SET-SYMBOL %symbol_1;\n//   #\"M3\" @PLACE(-5, 21) => @ORIENT(\"R270\");\n//   %symbol_2 = @DEFINE-SYMBOL(box=[-2, -6, 2, 0]) {\n//     @LINE {(1, -2.2) (-1, -2.2)};\n//     @LINE {(0, 0) (0, -2.2)};\n//     @LINE {(0, -2.8) (0, -6)};\n//     @ARC {(1, -3.2) (0, -2.8) (-1, -3.2)};\n//     ? @TEXT(\"${name}\", size=0.8, halign=\"start\", valign=\"baseline\", key=\"name\") => @PLACE(1.4, 0.1);\n//     ? @TEXT(\"${model}\", size=0.8, halign=\"end\", valign=\"baseline\", key=\"model\") => @PLACE(-1.1, 0.3);\n//     (0, 0) @PIN(terminal=1);\n//     (0, -6) @PIN(terminal=2)\n//   };\n//   #\"C1\" @SET-SYMBOL %symbol_2;\n//   #\"C1\" @PLACE(4, 1) => @ORIENT(\"R0\");\n//   #\"M10\" @SET-SYMBOL %symbol_1;\n//   #\"M10\" @PLACE(-51, 8) => @ORIENT(\"R0\");\n//   #\"M0\" @SET-SYMBOL %symbol_1;\n//   #\"M0\" @PLACE(-24, 8) => @ORIENT(\"R0\");\n//   #\"M2\" @SET-SYMBOL %symbol_0;\n//   #\"M2\" @PLACE(-24, -19) => @ORIENT(\"R0\");\n//   #\"M4\" @SET-SYMBOL %symbol_1;\n//   #\"M4\" @PLACE(20, 13) => @ORIENT(\"R90\");\n//   #\"M9\" @SET-SYMBOL %symbol_0;\n//   #\"M9\" @PLACE(-51, -10) => @ORIENT(\"R0\");\n//   %symbol_3 = @DEFINE-SYMBOL(box=[-0.1, -2.8, 3.8, 2.8]) {\n//     @LINE {(2, 1.5) (4, 1.5) (4, 3)};\n//     @LINE {(2, -1.5) (2, 1.5)};\n//     @LINE {(3, -1) (4, -1.5) (3, -2)};\n//     @LINE {(0, 0) (1.5, 0)};\n//     @LINE {(1.5, 1.5) (1.5, -1.5)};\n//     @LINE {(2, -1.5) (4, -1.5) (4, -3)};\n//     ? @TEXT(\"${name}\", size=0.8, halign=\"start\", valign=\"baseline\", key=\"name\") => @PLACE(5, 3.5);\n//     ? @TEXT(\"${model}\", size=0.8, halign=\"end\", valign=\"baseline\", key=\"model\") => @PLACE(3.2, 3.2);\n//     (4, -1) @PIN(terminal=4);\n//     (4, -3) @PIN(terminal=3);\n//     (4, 3) @PIN(terminal=1);\n//     (0, 0) @PIN(terminal=2)\n//   };\n//   #\"M7\" @SET-SYMBOL %symbol_3;\n//   #\"M7\" @PLACE(56, 21) => @ORIENT(\"MYR90\");\n//   #\"M8\" @SET-SYMBOL %symbol_3;\n//   #\"M8\" @PLACE(44, 21) => @ORIENT(\"MYR90\");\n//   #\"M5\" @SET-SYMBOL %symbol_0;\n//   #\"M5\" @PLACE(24, -3) => @ORIENT(\"MY\");\n//   #\"M6\" @SET-SYMBOL %symbol_0;\n//   #\"M6\" @PLACE(35, -9) => @ORIENT(\"MYR90\");\n//   %input_port_symbol = @DEFINE-SYMBOL(box=[-2, -1, 0, 1]) {\n//       @LINE(fill=\"linecolor\", linestyle=\"none\") {(-2, -0.9) (-2, 0.9) (-0.9, 0.9) (0, 0) (-0.9, -0.9) (-2, -0.9)};\n//       (0, 0) @PIN(terminal=1, direction=\"right\");\n//       ? @TEXT(\"${name}\", key=\"name\", halign=\"middle\") => @PLACE(-1, 1.6)\n//     };\n//   %port_0 = $IO_PORT @ATTACH &\"CLK\" => @PLACE(-65, 11) => @ORIENT(\"R0\");\n//   %port_0 @SET-SYMBOL %input_port_symbol;\n//   %port_0 @TEXT(\"CLK\", key=\"name\");\n//   %port_1 = $IO_PORT @ATTACH &\"VIN\" => @PLACE(-65, 8) => @ORIENT(\"R0\");\n//   %port_1 @SET-SYMBOL %input_port_symbol;\n//   %port_1 @TEXT(\"VIN\", key=\"name\");\n//   %port_2 = $IO_PORT @ATTACH &\"VDD\" => @PLACE(-66, 5) => @ORIENT(\"R0\");\n//   %port_3 = $IO_PORT @ATTACH &\"VSS\" => @PLACE(-66, 2) => @ORIENT(\"R0\");\n//   %output_port_symbol = @DEFINE-SYMBOL(box=[0, -1, 2, 1]) {\n//       @LINE(fill=\"linecolor\", linestyle=\"none\") {(0, -0.9) (0, 0.9) (1.1, 0.9) (2, 0) (1.1, -0.9) (0, -0.9)};\n//       (0, 0) @PIN(terminal=1, direction=\"left\");\n//       ? @TEXT(\"${name}\", key=\"name\", halign=\"middle\") => @PLACE(1, 1.6)\n//     };\n//   %port_4 = $IO_PORT @ATTACH &\"VBST\" => @PLACE(-67, -1) => @ORIENT(\"R0\");\n//   %port_4 @SET-SYMBOL %output_port_symbol;\n//   %port_4 @TEXT(\"VBST\", key=\"name\");\n//   %p0 = (35, -3);\n//   %wire_0 = &\"VBST\" @WIRE(mode=\"straight\") {#\"M5\":2 %p0};\n//   %p1 = (35, 18);\n//   %wire_1 = &\"VBST\" @WIRE(mode=\"straight\") {%p0 %p1};\n//   %p2 = (34, 17);\n//   %wire_2 = &\"VBST\" @WIRE(mode=\"straight\") {%p2 #\"M8\":1};\n//   %p3 = (34, 18);\n//   %wire_3 = &\"VBST\" @WIRE(mode=\"straight\") {%p2 %p3};\n//   %wire_4 = &\"VBST\" @WIRE(mode=\"straight\") {%p3 %p1};\n//   %wire_5 = &\"VBST\" @WIRE(mode=\"straight\") {#\"M6\":2 %p0};\n//   %wire_6 = &\"VBST\" @WIRE(mode=\"straight\") {#\"M4\":1 %p2};\n//   %p4 = (35, 28);\n//   %wire_7 = &\"VBST\" @WIRE(mode=\"straight\") {%p1 %p4};\n//   %p5 = (-5, 28);\n//   %wire_8 = &\"VBST\" @WIRE(mode=\"straight\") {#\"M3\":2 %p5};\n//   %wire_9 = &\"VBST\" @WIRE(mode=\"straight\") {%p5 %p4};\n//   %wire_1 @TEXT(\"VBST\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(34.3, 4);\n//   %wire_10 = &\"net7\" @WIRE(mode=\"straight\") {#\"M8\":3 #\"M7\":1};\n//   %p6 = (-55, -10);\n//   %p7 = (-55, 8);\n//   %wire_11 = &\"CLK\" @WIRE(mode=\"straight\") {%p6 %p7};\n//   %wire_12 = &\"CLK\" @WIRE(mode=\"straight\") {%p7 #\"M10\":2};\n//   %wire_13 = &\"CLK\" @WIRE(mode=\"straight\") {%p6 #\"M9\":2};\n//   %p8 = (-28, 8);\n//   %wire_14 = &\"CLK\" @WIRE(mode=\"straight\") {%p8 #\"M0\":2};\n//   %p9 = (-28, -6);\n//   %wire_15 = &\"CLK\" @WIRE(mode=\"straight\") {%p9 %p8};\n//   %wire_16 = &\"CLK\" @WIRE(mode=\"straight\") {%p9 #\"M1\":2};\n//   %wire_11 @TEXT(\"CLK\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(-55.7, -1);\n//   %wire_15 @TEXT(\"CLK\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(-28.7, 1);\n//   %wire_17 = &\"CLKN\" @WIRE(mode=\"straight\") {#\"M9\":1 #\"M10\":1};\n//   %p10 = (56, 24);\n//   %wire_18 = &\"CLKN\" @WIRE(mode=\"straight\") {#\"M7\":2 %p10};\n//   %p11 = (-29, -19);\n//   %wire_19 = &\"CLKN\" @WIRE(mode=\"straight\") {%p11 #\"M2\":2};\n//   %wire_17 @TEXT(\"CLKN\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(-47.7, -1);\n//   %wire_19 @TEXT(\"CLKN\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(-27, -18.3);\n//   %wire_18 @TEXT(\"CLKN\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(55.3, 23);\n//   %p12 = (-20, 17);\n//   %p13 = (-20, 23);\n//   %wire_20 = &\"VDD\" @WIRE(mode=\"straight\") {%p12 %p13};\n//   %p14 = (-47, 15);\n//   %wire_21 = &\"VDD\" @WIRE(mode=\"straight\") {#\"M10\":3 %p14};\n//   %p15 = (-43, 9);\n//   %wire_22 = &\"VDD\" @WIRE(mode=\"straight\") {#\"M10\":4 %p15};\n//   %p16 = (44, 24);\n//   %wire_23 = &\"VDD\" @WIRE(mode=\"straight\") {#\"M8\":2 %p16};\n//   %wire_24 = &\"VDD\" @WIRE(mode=\"straight\") {#\"M0\":3 %p12};\n//   %wire_25 = &\"VDD\" @WIRE(mode=\"straight\") {%p12 #\"M3\":1};\n//   %p17 = (-16, 9);\n//   %wire_26 = &\"VDD\" @WIRE(mode=\"straight\") {#\"M0\":4 %p17};\n//   %wire_21 @TEXT(\"VDD\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(-47.7, 14);\n//   %wire_22 @TEXT(\"VDD\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(-45, 9.7);\n//   %wire_20 @TEXT(\"VDD\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(-20.7, 21);\n//   %wire_26 @TEXT(\"VDD\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(-18, 9.7);\n//   %wire_23 @TEXT(\"VDD\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(43.3, 23);\n//   %p18 = (48, -13);\n//   %wire_27 = &\"VIN\" @WIRE(mode=\"straight\") {#\"M6\":3 %p18};\n//   %wire_27 @TEXT(\"VIN\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(45, -12.3);\n//   %p19 = (-47, -18);\n//   %wire_28 = &\"VSS\" @WIRE(mode=\"straight\") {%p19 #\"M9\":3};\n//   %p20 = (-43, -11);\n//   %wire_29 = &\"VSS\" @WIRE(mode=\"straight\") {#\"M9\":4 %p20};\n//   %p21 = (63, 17);\n//   %wire_30 = &\"VSS\" @WIRE(mode=\"straight\") {#\"M7\":3 %p21};\n//   %p22 = (57, 13);\n//   %wire_31 = &\"VSS\" @WIRE(mode=\"straight\") {%p22 #\"M7\":4};\n//   %p23 = (36, -16);\n//   %wire_32 = &\"VSS\" @WIRE(mode=\"straight\") {%p23 #\"M6\":4};\n//   %p24 = (45, 13);\n//   %wire_33 = &\"VSS\" @WIRE(mode=\"straight\") {%p24 #\"M8\":4};\n//   %p25 = (16, -4);\n//   %wire_34 = &\"VSS\" @WIRE(mode=\"straight\") {%p25 #\"M5\":4};\n//   %p26 = (-20, -26);\n//   %wire_35 = &\"VSS\" @WIRE(mode=\"straight\") {%p26 #\"M2\":3};\n//   %p27 = (-16, -20);\n//   %wire_36 = &\"VSS\" @WIRE(mode=\"straight\") {#\"M2\":4 %p27};\n//   %p28 = (-16, -7);\n//   %wire_37 = &\"VSS\" @WIRE(mode=\"straight\") {#\"M1\":4 %p28};\n//   %wire_28 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(-47.7, -16);\n//   %wire_29 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(-45, -10.3);\n//   %wire_37 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(-18, -6.3);\n//   %wire_36 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(-18, -19.3);\n//   %wire_34 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(17, -3.3);\n//   %wire_32 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(35.3, -15);\n//   %wire_33 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(44.3, 15);\n//   %wire_31 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(56.3, 15);\n//   %wire_30 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\") => @PLACE(61, 17.7);\n//   %wire_35 @TEXT(\"VSS\", size=1, halign=\"middle\", valign=\"baseline\", rotate=90) => @PLACE(-20.7, -24);\n//   %p29 = (-1, 14);\n//   %p30 = (-1, 17);\n//   %wire_38 = &\"net1\" @WIRE(mode=\"straight\") {%p29 %p30};\n//   %p31 = (4, 17);\n//   %wire_39 = &\"net1\" @WIRE(mode=\"straight\") {%p30 %p31};\n//   %p32 = (-4, 14);\n//   %wire_40 = &\"net1\" @WIRE(mode=\"straight\") {%p32 %p29};\n//   %wire_41 = &\"net1\" @WIRE(mode=\"straight\") {%p32 #\"M3\":4};\n//   %p33 = (16, 20);\n//   %p34 = (19, 20);\n//   %wire_42 = &\"net1\" @WIRE(mode=\"straight\") {%p33 %p34};\n//   %p35 = (16, 17);\n//   %wire_43 = &\"net1\" @WIRE(mode=\"straight\") {%p35 %p33};\n//   %wire_44 = &\"net1\" @WIRE(mode=\"straight\") {%p31 %p35};\n//   %wire_45 = &\"net1\" @WIRE(mode=\"straight\") {#\"C1\":1 %p31};\n//   %wire_46 = &\"net1\" @WIRE(mode=\"straight\") {#\"M4\":4 %p34};\n//   %wire_47 = &\"net1\" @WIRE(mode=\"straight\") {#\"M3\":3 %p30};\n//   %wire_48 = &\"net1\" @WIRE(mode=\"straight\") {%p35 #\"M4\":3};\n//   %p36 = (20, -13);\n//   %wire_49 = &\"net2\" @WIRE(mode=\"straight\") {%p36 #\"M6\":1};\n//   %wire_50 = &\"net2\" @WIRE(mode=\"straight\") {%p36 #\"M5\":3};\n//   %p37 = (-20, -13);\n//   %p38 = (4, -13);\n//   %wire_51 = &\"net2\" @WIRE(mode=\"straight\") {%p37 %p38};\n//   %wire_52 = &\"net2\" @WIRE(mode=\"straight\") {%p37 #\"M1\":3};\n//   %wire_53 = &\"net2\" @WIRE(mode=\"straight\") {%p38 #\"C1\":2};\n//   %wire_54 = &\"net2\" @WIRE(mode=\"straight\") {%p38 %p36};\n//   %wire_55 = &\"net2\" @WIRE(mode=\"straight\") {#\"M2\":1 %p37};\n//   %p39 = (-20, 3);\n//   %p40 = (20, 3);\n//   %wire_56 = &\"net3\" @WIRE(mode=\"straight\") {%p39 %p40};\n//   %wire_57 = &\"net3\" @WIRE(mode=\"straight\") {%p40 #\"M4\":2};\n//   %wire_58 = &\"net3\" @WIRE(mode=\"straight\") {%p39 #\"M0\":1};\n//   %wire_59 = &\"net3\" @WIRE(mode=\"straight\") {#\"M5\":1 %p40};\n//   %wire_60 = &\"net3\" @WIRE(mode=\"straight\") {#\"M1\":1 %p39};\n//   $JUNCTION @ATTACH %p0;\n//   $JUNCTION @ATTACH %p1;\n//   $JUNCTION @ATTACH %p2;\n//   $JUNCTION @ATTACH %p12;\n//   $JUNCTION @ATTACH %p30;\n//   $JUNCTION @ATTACH %p31;\n//   $JUNCTION @ATTACH %p35;\n//   $JUNCTION @ATTACH %p36;\n//   $JUNCTION @ATTACH %p37;\n//   $JUNCTION @ATTACH %p38;\n//   $JUNCTION @ATTACH %p39;\n//   $JUNCTION @ATTACH %p40\n// }\nends RS9_L5_9bit_Bootstrap\n"},{"id":"inverter-dut","kind":"public_circuit","label":"Public independent CMOS_INVERTER circuit with newly authored geometry","path":"data/sources/inverter-dut.publication.scs","originalSourceSha256":"05b72c015ebaec66d1fdf0700fe425d6c03c1791c1e4758ee2fb392544d2ffc7","publicationSha256":"d41d38c83de57359ea4bc9ecd07e0976cc4f9b2796e9eb8ceed235565e441db8","transformation":"Added two publication notice comment lines; the source body is unchanged.","submitted":false,"sourceRepositoryPath":"lib/basics/cmos-inverter/schematic/cmos-inverter.scs","filename":"inverter-dut.publication.scs","text":"// PUBLICATION EXCERPT — explanatory copy, not a submitted simulator input.\n// Original source identity and this derived copy have separate hashes in source-manifest.json.\n// Independent two-device inverter extracted from public circuit source.\n// No device implementation, operating condition or simulation configuration is included.\n// @RNL(type=\"header\") {\n//   @VERSION(\"0.3\");\n//   @USE-PACKAGE(name=\"eda-symbol\", version=\"0.1\", path=\"eda-symbol.rnl\");\n//   @USE-PACKAGE(name=\"eda-style\", version=\"0.1\", path=\"eda-style.rnl\")\n// }\nsimulator lang=spectre\nsubckt CMOS_INVERTER CLK CLKN VDD VSS\n    M9 (CLKN CLK VSS VSS) nch_ulvt_mac l=30n w=250.0n multi=1 nf=1 sd=100n \\\n        ad=1.875e-14 as=1.875e-14 pd=650.0n ps=650.0n nrd=1.338763 \\\n        nrs=1.338763 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n    M10 (CLKN CLK VDD VDD) pch_ulvt_mac l=30n w=250.0n multi=1 nf=1 \\\n        sd=100n ad=1.875e-14 as=1.875e-14 pd=650.0n ps=650.0n nrd=0.777777 \\\n        nrs=0.777777 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\n// @RNL(author=\"circuitgit-starter\") {\n//   #\"M10\" @SET-SYMBOL $PMOS_4TERM;\n//   #\"M10\" @PLACE(0, 10) => @ORIENT(\"R0\");\n//   #\"M9\" @SET-SYMBOL $NMOS_4TERM;\n//   #\"M9\" @PLACE(0, -10) => @ORIENT(\"R0\");\n//   %in = $IO_PORT @ATTACH &\"CLK\" => @PLACE(-18, 0) => @ORIENT(\"R0\");\n//   %out = $IO_PORT @ATTACH &\"CLKN\" => @PLACE(26, 0) => @ORIENT(\"R180\");\n//   %vdd = $IO_PORT @ATTACH &\"VDD\" => @PLACE(0, 22) => @ORIENT(\"R270\");\n//   %vss = $IO_PORT @ATTACH &\"VSS\" => @PLACE(0, -22) => @ORIENT(\"R90\");\n//   %gin = (-10, 0);\n//   %gp = (-10, 10);\n//   %gn = (-10, -10);\n//   %d = (0, 0);\n//   %vp = (0, 18);\n//   %vn = (0, -18);\n//   %bp = (18, 10);\n//   %bprail = (18, 18);\n//   %bn = (18, -10);\n//   %bnrail = (18, -18);\n//   &\"CLK\" @WIRE(mode=\"straight\") {%in:1 %gin};\n//   &\"CLK\" @WIRE(mode=\"straight\") {%gp %gin %gn};\n//   &\"CLK\" @WIRE(mode=\"straight\") {%gp #\"M10\":2};\n//   &\"CLK\" @WIRE(mode=\"straight\") {%gn #\"M9\":2};\n//   &\"CLKN\" @WIRE(mode=\"straight\") {#\"M10\":1 %d #\"M9\":1};\n//   &\"CLKN\" @WIRE(mode=\"straight\") {%d %out:1};\n//   &\"VDD\" @WIRE(mode=\"straight\") {#\"M10\":3 %vp %vdd:1};\n//   &\"VDD\" @WIRE(mode=\"straight\") {#\"M10\":4 %bp %bprail %vp};\n//   &\"VSS\" @WIRE(mode=\"straight\") {#\"M9\":3 %vn %vss:1};\n//   &\"VSS\" @WIRE(mode=\"straight\") {#\"M9\":4 %bn %bnrail %vn}\n// }\nends CMOS_INVERTER\n"},{"id":"bootstrap-tb","kind":"authored_testbench","label":"Frozen Bootstrap authored testbench, review revision 10","path":"data/sources/bootstrap-tb.publication.scs","originalSourceSha256":"3171d9d01e3e2becfa3c93c7a5fa9bfac8a565109e0a3090aa7aa28234941137","publicationSha256":"d4f09ec705c26558648ac6131532cfb5e1e2e1df66bc44255645af0b888d2e0c","transformation":"Added two publication notice comment lines; the source body is unchanged.","submitted":false,"sourceRepositoryPath":null,"filename":"bootstrap-tb.publication.scs","text":"// PUBLICATION EXCERPT — explanatory copy, not a submitted simulator input.\n// Original source identity and this derived copy have separate hashes in source-manifest.json.\nsimulator lang=spectre\nglobal 0\ninclude \"pdk.scs\"\ninclude \"dut.scs\"\n// Explicit proposed standalone sample-and-hold experiment.\n// The DUT output VBST drives MSAMPLE; spectral data use VOUT.\nparameters VDDVAL=1 VCM=0.5 AIN=0.45 FS=1G FIN=442138671.875\nVDD0 (VDD 0) vsource type=dc dc=VDDVAL\nVIN0 (VIN 0) vsource type=sine sinedc=VCM ampl=AIN freq=FIN\nVCLK0 (CLK 0) vsource type=pulse val0=0 val1=VDDVAL delay=0 rise=10p fall=10p width=230p period=1n\nXDUT (CLK VBST VDD VIN 0) RS9_L5_9bit_Bootstrap\n    MSAMPLE (VIN VBST VOUT 0) nch_lvt_mac l=30n w=500n multi=1 nf=1 sd=100n \\\n        ad=3.75e-14 as=3.75e-14 pd=1.15u ps=1.15u nrd=0.662935 \\\n        nrs=0.662935 sa=75.0n sb=75.0n sa1=75.0n sa2=75.0n sa3=75.0n \\\n        sa4=75.0n sb1=75.0n sb2=75.0n sb3=75.0n spa=100n spa1=100n \\\n        spa2=100n spa3=100n sap=91.9776n sapb=114.444n spba=115.715n \\\n        spba1=117.043n dfm_flag=0 spmt=1.11111e+15 spomt=0 \\\n        spomt1=1.11111e+60 spmb=1.11111e+15 spomb=0 spomb1=1.11111e+60\nCHOLD (VOUT 0) capacitor c=100f\nRLEAK (VOUT 0) resistor r=1G\nsimulatorOptions options reltol=1e-5 vabstol=1e-8 iabstol=1e-14 temp=27 tnom=27 gmin=1e-12\nsaveOptions options save=selected\ntran_bootstrap tran stop=4.15975u maxstep=5p errpreset=conservative method=gear2only skipstart=64n skipstop=4.15975u strobedelay=750p strobeperiod=1n strobeoutput=strobeonly outputstart=64.75n\nsave CLK VIN VOUT VBST VDD VDD0:p\n"},{"id":"bootstrap-evidence","kind":"allowlist_summary","label":"Manually constructed summary of the frozen review; no runtime dump","path":"data/sources/bootstrap-evidence.publication.json","publicationSha256":"17b278721a52a292232a7ea1454ee335d382a6ed35124ad5b28a171939ff69b4","transformation":"Manually selected facts from source and revision-10 review. Private fields and model implementations omitted.","submitted":false,"filename":"bootstrap-evidence.publication.json","text":"{\n  \"schema\": \"ic.workflow.public-evidence.v1\",\n  \"observedOn\": \"2026-11-24\",\n  \"case\": \"bootstrap-sample-hold\",\n  \"status\": {\n    \"revision\": 10,\n    \"state\": \"awaiting_human_review\",\n    \"humanReview\": \"pending\",\n    \"solverStarted\": false,\n    \"jobPresent\": false,\n    \"waveformDataPresent\": false,\n    \"fftResultsPresent\": false\n  },\n  \"circuit\": {\n    \"name\": \"RS9_L5_9bit_Bootstrap\",\n    \"scope\": \"Bootstrap gate driver from a 9-bit SAR ADC design, with an explicitly added MSAMPLE/CHOLD sample-and-hold testbench.\",\n    \"ports\": [\n      \"CLK\",\n      \"VBST\",\n      \"VDD\",\n      \"VIN\",\n      \"VSS\"\n    ],\n    \"mosCount\": 11,\n    \"capacitorCount\": 1,\n    \"geometry\": \"Embedded @RNL in the .scs source; no independent DUT .rnl is claimed.\",\n    \"geometryFidelity\": \"partial\"\n  },\n  \"requiredModels\": [\n    \"cfmom_2t\",\n    \"nch_lvt_mac\",\n    \"nch_ulvt_mac\",\n    \"pch_ulvt_mac\"\n  ],\n  \"selection\": {\n    \"sections\": [\n      \"pre_simu\",\n      \"TTMacro_MOS_MOSCAP\",\n      \"TT_MOM\"\n    ],\n    \"tmiLabel\": \"RH_64/libTMImodel.so\",\n    \"reason\": \"Stored remote x86_64 CentOS/RHEL-family observation supports this platform choice; runtime binary compatibility and process suitability remain separate checks.\",\n    \"cachedScopeFiles\": 3,\n    \"cachedScopes\": 108,\n    \"cachedNamespaceModels\": 91,\n    \"cachedSelectionReadyToRun\": false\n  },\n  \"freshStaticAudit\": {\n    \"schema\": \"spiceowl.model-dependency-audit.v1\",\n    \"status\": \"complete_for_supported_static_syntax\",\n    \"files\": 4,\n    \"includesDynamicLibraryInFileCount\": true,\n    \"scopes\": 108,\n    \"namespaceModelsFromSelectedCatalog\": 91,\n    \"requiredModelsMatched\": 4,\n    \"missingRequiredModels\": 0,\n    \"unknownDirectives\": 0,\n    \"comparisonSchema\": \"spiceowl.pdk-scope-audit-comparison.v1\",\n    \"comparisonStatus\": \"matched\",\n    \"matchesSelection\": true,\n    \"staticOnly\": true,\n    \"readyToRun\": false,\n    \"humanReviewRequired\": true,\n    \"scientificCompatibility\": \"not_evaluated\",\n    \"dynamicLoaderExecutionProven\": false,\n    \"qualificationScope\": \"Selected include closure only; neither whole-PDK qualification nor simulator execution.\",\n    \"scaleFactorNote\": \"Repeated geoshrink literals are recorded without assuming an effective value or equating distinct source options.\"\n  },\n  \"testbench\": {\n    \"supplyV\": 1,\n    \"inputOffsetV\": 0.5,\n    \"inputPeakV\": 0.45,\n    \"clockHz\": 1000000000,\n    \"clockRiseS\": 1e-11,\n    \"clockFallS\": 1e-11,\n    \"clockWidthS\": 2.3e-10,\n    \"inputHz\": 442138671.875,\n    \"coherentBin\": 1811,\n    \"sampleCount\": 4096,\n    \"sampleRateHz\": 1000000000,\n    \"firstSampleS\": 6.475e-08,\n    \"lastSampleS\": 4.15975e-06,\n    \"samplePeriodS\": 1e-09,\n    \"warmupCycles\": 64,\n    \"strobeDelayS\": 7.5e-10,\n    \"maxStepS\": 5e-12,\n    \"holdCapacitanceF\": 1e-13,\n    \"leakResistanceOhm\": 1000000000.0,\n    \"temperatureC\": 27,\n    \"nominalTemperatureC\": 27,\n    \"method\": \"gear2only\",\n    \"errpreset\": \"conservative\",\n    \"transientNoiseEnabled\": false,\n    \"savedSignals\": [\n      \"CLK\",\n      \"VIN\",\n      \"VOUT\",\n      \"VBST\",\n      \"VDD\",\n      \"VDD0:p\"\n    ],\n    \"spectrumSignal\": \"VOUT\",\n    \"referenceSignal\": \"VIN\",\n    \"window\": \"rectangular\",\n    \"binHalfWidth\": 0,\n    \"fullScalePeakV\": 0.5,\n    \"noiseFloorMethod\": 3,\n    \"interpolationAllowed\": false,\n    \"missingOrExtraSamples\": \"stop_with_data_gap_no_implicit_crop\",\n    \"numericalAcceptanceTargets\": []\n  },\n  \"resources\": {\n    \"spectreThreads\": 1,\n    \"poolMinimumAvailableMemoryBytes\": 536870912,\n    \"memoryHardLimitBytes\": null,\n    \"wallTimeHardLimitSeconds\": null,\n    \"boundary\": \"Pool admission and +mt=1 do not enforce an OS memory cap or wall-time cap.\"\n  },\n  \"publication\": {\n    \"kind\": \"manually constructed allowlist summary\",\n    \"excludes\": [\n      \"accounts\",\n      \"host aliases\",\n      \"absolute filesystem paths\",\n      \"private receipt identities\",\n      \"credentials\",\n      \"licensed model implementations\"\n    ],\n    \"historicalResultsReused\": false\n  }\n}\n"},{"id":"source-manifest","kind":"publication_manifest","label":"Public source identities and transformations","filename":"source-manifest.json","publicationSha256":"676a0cf396562560b9c343dd165dc427c8a212b300081b59c36d57d01fdc0a70","submitted":false,"text":"{\n  \"schema\": \"ic.workflow.source-manifest.v1\",\n  \"publicationOnly\": true,\n  \"files\": [\n    {\n      \"id\": \"bootstrap-dut\",\n      \"kind\": \"public_circuit\",\n      \"label\": \"Public RS9_L5_9bit_Bootstrap circuit with embedded @RNL geometry\",\n      \"path\": \"data/sources/bootstrap-dut.publication.scs\",\n      \"originalSourceSha256\": \"9b879b2fb1c6918606317875f3bfc1370f2c6380b34c747c013a3cc9ff5f78b6\",\n      \"publicationSha256\": \"808da690214b9e8ee1c0b2ce05c4dbf7eda69d6d71a198776d3cd5c53597b2c0\",\n      \"transformation\": \"Added two publication notice comment lines; the source body is unchanged.\",\n      \"submitted\": false,\n      \"sourceRepositoryPath\": \"lib/pipetisar-real/RS9_L5_9bit_Bootstrap/schematic/RS9_L5_9bit_Bootstrap.scs\"\n    },\n    {\n      \"id\": \"inverter-dut\",\n      \"kind\": \"public_circuit\",\n      \"label\": \"Public independent CMOS_INVERTER circuit with newly authored geometry\",\n      \"path\": \"data/sources/inverter-dut.publication.scs\",\n      \"originalSourceSha256\": \"05b72c015ebaec66d1fdf0700fe425d6c03c1791c1e4758ee2fb392544d2ffc7\",\n      \"publicationSha256\": \"d41d38c83de57359ea4bc9ecd07e0976cc4f9b2796e9eb8ceed235565e441db8\",\n      \"transformation\": \"Added two publication notice comment lines; the source body is unchanged.\",\n      \"submitted\": false,\n      \"sourceRepositoryPath\": \"lib/basics/cmos-inverter/schematic/cmos-inverter.scs\"\n    },\n    {\n      \"id\": \"bootstrap-tb\",\n      \"kind\": \"authored_testbench\",\n      \"label\": \"Frozen Bootstrap authored testbench, review revision 10\",\n      \"path\": \"data/sources/bootstrap-tb.publication.scs\",\n      \"originalSourceSha256\": \"3171d9d01e3e2becfa3c93c7a5fa9bfac8a565109e0a3090aa7aa28234941137\",\n      \"publicationSha256\": \"d4f09ec705c26558648ac6131532cfb5e1e2e1df66bc44255645af0b888d2e0c\",\n      \"transformation\": \"Added two publication notice comment lines; the source body is unchanged.\",\n      \"submitted\": false,\n      \"sourceRepositoryPath\": null\n    },\n    {\n      \"id\": \"bootstrap-evidence\",\n      \"kind\": \"allowlist_summary\",\n      \"label\": \"Manually constructed summary of the frozen review; no runtime dump\",\n      \"path\": \"data/sources/bootstrap-evidence.publication.json\",\n      \"publicationSha256\": \"17b278721a52a292232a7ea1454ee335d382a6ed35124ad5b28a171939ff69b4\",\n      \"transformation\": \"Manually selected facts from source and revision-10 review. Private fields and model implementations omitted.\",\n      \"submitted\": false\n    }\n  ]\n}\n"}]);
export const PUBLIC_EVIDENCE = JSON.parse(PUBLICATION_SOURCES.find(source => source.id === "bootstrap-evidence").text);
export const CHECKPOINT = Object.freeze({revision: 10, state: 'awaiting_human_review',
  evidence: 'reviewed_publication_allowlist_snapshot_2026_11_24', solver_result_available: false,
  measurement_result_available: false});
export const CIRCUITS = Object.freeze({
  bootstrap: {title: 'RS9_L5_9bit_Bootstrap', ports: ['CLK', 'VBST', 'VDD', 'VIN', 'VSS'],
    models: ['cfmom_2t', 'nch_lvt_mac', 'nch_ulvt_mac', 'pch_ulvt_mac'],
    sections: ['pre_simu', 'TTMacro_MOS_MOSCAP', 'TT_MOM'], samples: 4096,
    note: '公开五端口结构 + 用户指定的外接采样测试。VBST 是栅极驱动，不是采样输出。',
    caption: '结构示意：Bootstrap 输出 VBST，驱动外接 MSAMPLE；CHOLD 保存采样节点。端序、器件尺寸与测试数值以受审 TB 为准。'},
  inverter: {title: 'CMOS_INVERTER', ports: ['CLK', 'CLKN', 'VDD', 'VSS'],
    models: ['nch_ulvt_mac', 'pch_ulvt_mac'], sections: null, samples: null,
    note: '公开 M9 / M10 的两管反相器。供电、激励、负载和模型绑定需要另行声明。',
    caption: '公开连接关系的重新绘制：两管共栅 CLK、共漏 CLKN；PMOS 源体接 VDD，NMOS 源体接 VSS。不是原 Virtuoso 布局或仿真结果。'},
});
export const STAGES = Object.freeze([
  {title: '选择接入模式', short: '双模式架构', tag: '01 / AGENT OWNERSHIP', subtitle: '独立入口或同一条会话',
    description: '先确定谁拥有 AI 对话与工具路由，再开始整理实验。独立模式与嵌入模式共享仿真引擎；切换本页只改变说明，不启动 Agent，也不改变任何配置。',
    ai: '理解任务，使用当前模式实际提供的工具。', engine: '保留模式、会话归属和能力状态；连接未验证就保持未验证。',
    guard: '嵌入 CircuitGit 时复用现有同一条 chat 和工具，不另建第二个 Agent 服务、数据库或执行账本。'},
  {title: '读取网表与静态结构', short: '网表静态提取', tag: '02 / SOURCE INVENTORY', subtitle: '先保留原字节与端口',
    description: '从用户提供的源字节提取 subckt、端口、实例和外部模型引用。完整保留参数与源身份；模型名字本身不能证明引脚顺序、器件极性或可用工艺。',
    ai: '解释结构，指出缺失材料；不凭模型名字补齐电气语义。', engine: 'inspect_agent_source 生成有界源清单与 netlist-model-requirements 契约。',
    guard: '公开结构不是 PDK 实现。这里展示端口与模型引用，不复制模型正文或生成替代模型。'},
  {title: '选择服务器与执行环境', short: '服务器与环境', tag: '03 / EXPLICIT HOST POOL', subtitle: '身份、版本、资源、ABI',
    description: '只检查用户明确选择的 SSH 路由。确认远端身份，观察 Spectre 版本、环境激活结果和平台，再根据请求评估 CPU 与内存余量。可用资源只是带时间的观察，不是资源预留。',
    ai: '从允许的服务器集合中解释候选和排除原因。', engine: '有界 SSH 检查、身份验证和资源策略；请求后保留至少 20% CPU / RAM 余量。',
    guard: '本页的 pool-a / pool-b 都是脱敏示意名。失败、过期或未知观察不能变成“空闲服务器”；平台 ABI 取自远端证据。'},
  {title: '选择 PDK 缓存与 section', short: 'PDK 全库与缓存', tag: '04 / OBSERVED DEPENDENCIES', subtitle: '首次扫描 · 后续查 LUT',
    description: '第一次在授权根目录内做有界完整库扫描；后续先查 SQLite LUT 的器件倒排索引。多器件需要同一入口的多个 section 时保留明确顺序，并按共同平台选择 TMI。',
    ai: '解释缓存覆盖、section 和候选差异；缺模型时请求新证据。', engine: '绑定库、主机、根目录与指纹；组合已观察到的作用域，拒绝命名冲突和不完整闭包。',
    guard: '缓存命中可以避免再次远程扫描，不代表当前文件仍新鲜。本页没有毫秒级性能实测，也不把 RH_64 名称当成二进制加载验证。'},
  {title: '核对 Verilog-A 与工具头文件', short: '受审辅助模型', tag: '05 / REVIEWED HELPERS', subtitle: '可选 helper 与标准头闭包',
    description: '需要行为辅助模块时，保留受审 ahdl_include 和相对 helper 文件的原字节。constants.vams、disciplines.vams 等标准头由所选工具环境观察和固定，执行时按闭包 staging。',
    ai: '提出确有必要的辅助模块并解释其作用。', engine: '检查相对 include、helper 来源、哈希与可支持语法；保持 DUT 支持文件和 TB helper 的角色区分。',
    guard: '本次 Bootstrap 测试台没有 ahdl_include，Verilog-A 分支为 not_applicable。其他实验仍须审核 helper / header 闭包；PDK 和工具标准头正文不进入公开源码或公开结果归档。'},
  {title: '把目标写成 Agent-TB', short: '实验目标与 TB', tag: '06 / DECLARED EXPERIMENT', subtitle: '测什么，必须先说清楚',
    description: '把人的研究目标写成完整 TB、显式刺激、保存信号和后处理契约。Bootstrap 的 VBST 只驱动采样管；要评估采样性能，需要外接 MSAMPLE 与 CHOLD，并在受审采样窗中取得 4096 个相干点。',
    ai: '撰写与解释 TB 草案，列出未定条件，提交可审核的完整差异。', engine: '静态检查源、TB、接口、分析、save 和 criteria，固定完整草案哈希；不生成缺失的科学目标。',
    guard: '供电、物理尺寸、频率、负载、预热和采样相位都不能猜。没有冻结验收阈值时，测量也不能自动变成通过。'},
  {title: '执行 fresh dependency audit', short: '重新审计依赖', tag: '07 / FRESH AUDIT', subtitle: '缓存选择与实际依赖对齐',
    description: '在最终 section / TMI / helper 组合确定后重新审计依赖，再与缓存选择比较。源文件哈希、完整模型 namespace 和声明冲突分别检查；缓存一致不替代远端新鲜度绑定。',
    ai: '说明失败或缺失的依赖项，保留原选择与审计身份。', engine: 'verify_scope_selection_audit 比较文件 pins、作用域与声明证据；执行所有者绑定审计来源和时间。',
    guard: '匹配的静态审计不证明模型的物理适用性或许可可用性。任何 hash / namespace 冲突都不能靠挑选旧回执绕过。'},
  {title: '冻结材料，等待人工审核', short: '人工审核闸门', tag: '08 / HUMAN REVIEW', subtitle: '本次实战到达的停止点',
    description: '将 TB 原字节、实验条件、PDK 绑定、资源范围、保存输出和验收条件放进同一个冻结审核。当前 Bootstrap 实战可确认的状态是 revision 10：awaiting_human_review。',
    ai: '给出清楚的变更说明和证据，提交审核请求后等待。', engine: '保持冻结对象、revision 和审核身份；只有执行所有者认可的人类审核结果可以推进。',
    guard: '本页没有批准或运行按钮。Agent 不能伪造 confirmed / approved 标志、代替用户签字，或将准备成功当成求解已经完成。'},
  {title: '独立 Spectre 执行与观察', short: '求解与持续观察', tag: '09 / ONE EXECUTION OWNER', subtitle: '保持原始 job / run 身份',
    description: '通过审核后，现有 Runner 在所选服务器上执行独立 Spectre 网表，不需要启动 Virtuoso。连接断开时保留原 submission key 和 job/run 身份，先查询原任务，不重新发起一次。',
    ai: '解释状态和确定性诊断，提出有依据的后续动作。', engine: '冻结输入、有限 staging、执行与收集；分别报告执行、数据有效性、功能、工作区/条件和依赖。',
    guard: '本次未提供 solver 运行回执。本阶段展示后续接口，而不是已发生的执行。SSH 断开不证明进程失败，退出码 0 也不证明实验达标。'},
  {title: '解析 PSF，按契约测量', short: '波形与科学指标', tag: '10 / MEASUREMENT CONTRACT', subtitle: '有实际数据，才有数字',
    description: '绑定原 run、原始归档、PSF 数据集和保存信号后再计算。SpiceOwl / ADCToolbox 的选择、算法版本、采样窗和参数都留在报告里。频谱、AC、DC 和瞬态各用自己的数据与契约。',
    ai: '选择适合研究问题的已支持分析，说明结果和局限。', engine: '解析支持的 PSF ASCII；对完整数据执行固定算法和显式 criteria，未知指标保持 null。',
    guard: '当前没有本次波形、FFT 或性能实测。不会从 VBST 图形推导采样 SNDR，也不会用瞬态样本伪造 AC Bode 曲线或通用化原始 region 代码。'},
]);
export const RESPONSIBILITIES = Object.freeze([
  ['目标与标准', '定义研究问题、条件、采样窗与接受阈值。', '澄清问题，提出可审阅草案。', '验证契约完整性，固定原始字节与哈希。', '从模型名猜电压、尺寸、极性或通过阈值。'],
  ['环境与 PDK', '选择授权服务器、模型范围和候选。', '解释候选证据、资源与缺失项。', '身份检查、显式扫描、LUT 查询、闭包和 ABI 检查。', '扫描未授权主机、泄露路径或把缓存当成新鲜审计。'],
  ['TB 与 helper', '审阅电路、刺激、输出和辅助代码。', '编写草案，解释 MSAMPLE / CHOLD 等测试构造。', '支持语法、include 图、角色和文件指纹检查。', '公开 PDK / 工具头正文，替换原源文件而不留记录。'],
  ['审核与执行', '在实际执行所有者提供的审核流程中决定。', '请求审核；失败或未知时解释证据。', '核对冻结审核与原始提交身份，由现有 Runner 执行。', 'Agent 自批、丢失任务身份、超时后换 key 重提。'],
  ['测量与结论', '确认测量定义与科学判断。', '引用实际数据与算法报告。', '验证数据、执行数值算法、逐指标比较。', '杜撰 FFT 指标、把 undefined 当 pass 或偷偷放宽 criteria。'],
]);

const example = payload => ({example: true, display_only: true,
  evidence_kind: 'contract_illustration_not_service_receipt', ...payload});
const bindings = () => ({intake_id: null, revision: null, source_sha256: null,
  note: '实际调用须复制当前服务返回的原始身份；本页不生成可执行替代 ID。'});
const tool = (name, argumentsShape) => `// 工具调用形状说明，不从本页执行。\n${name}\n${JSON.stringify(example(argumentsShape), null, 2)}`;

export function stageView(index, options = {}) {
  if (!Number.isInteger(index) || index < 0 || index >= STAGES.length) throw new RangeError('Unknown stage');
  const circuitKey = options.circuit === 'inverter' ? 'inverter' : 'bootstrap';
  const circuit = CIRCUITS[circuitKey], bootstrap = circuitKey === 'bootstrap';
  const mode = options.mode === 'standalone' ? 'standalone' : 'embedded_in_circuitgit';
  const cache = options.cache === 'first_scan' ? 'first_scan' : 'cached';
  const operation = ['spec_analyze', 'coherent_fft', 'ac_metrics', 'dc_op_metrics', 'tran_metrics'].includes(options.analysis) ? options.analysis : 'spec_analyze';
  const view = {...STAGES[index], input: {}, output: {}, tools: '', ui: [],
    status: index === 0 ? '已实现 · 真机推理待验证' : index === 7 && bootstrap ? '已确认：待人工审核' : index > 7 ? 'planned · 尚未运行' : bootstrap ? '已审公开静态材料' : '公开结构 / 待配置',
    statusKind: index === 7 && bootstrap ? 'review' : index > 7 ? 'pending' : '',
    empty: index > 7 ? '等待本次原始运行数据与回执' : null};
  switch (index) {
    case 0:
      view.input = {mode, simulation_engine: 'spiceowl', requested_action: 'explain_workflow'};
      view.output = {mode, conversation_owner: mode === 'embedded_in_circuitgit' ? 'CircuitGit existing chat' : 'explicit standalone connection',
        engine_shared: true, connection_status: 'not_checked', model_inference_observed: false,
        implementation_status: 'implemented', fake_protocol_tests: 'passed', real_provider_inference: 'unknown',
        starts_agent_on_page_open: false, second_embedded_agent_service: false};
      view.ui = [['模式选择', mode === 'embedded_in_circuitgit' ? '使用 CircuitGit 的同一条对话和现有工具目录，embedded 模式不会启动第二个 Agent。' : 'spiceowl serve 默认 standalone；打开界面后需要显式 Connect 才启动本地 Codex app-server。'],
        ['引擎能力', '确定性仿真 SDK / CLI 不依赖模型在线。'], ['数据边界', 'CircuitGit 继续使用自己的 Runner / Supervision；optional ReadViews 只读已有 SpiceOwl 历史库。']];
      view.ui.push(['验证层级', '双模式接口已实现，fake protocol 测试通过；真实订阅认证与模型推理尚未在本页材料中证实。']);
      view.tools = mode === 'embedded_in_circuitgit'
        ? '# 本地控制台入口：此模式不启动 Agent\nspiceowl serve --agent-mode embedded_in_circuitgit\n\n# 嵌入方把有限工具导出到既有会话\nfrom spiceowl.agent_channel import export_simulation_management_tools\ntools = export_simulation_management_tools(mode="embedded_in_circuitgit")\n\n# CircuitGit 既有工具入口：ic_onboarding_session\n# 运行 / 收集仍由已配置的原 Runner 与人工审核链拥有。'
        : '# standalone 是 serve 的默认模式\nspiceowl serve --agent-mode standalone\n\n# 可选配置；值由操作者提供，不能从示例猜测\n# --codex-executable CODEX_EXECUTABLE\n# --codex-model MODEL\n# --pdk-lut PRIVATE_LUT\n# --circuitgit-client-config PRIVATE_CLIENT_CONFIG\n# --circuitgit-sdk-path CIRCUITGIT_SDK\n\n# 界面显式 Connect 后才启动本地 Codex app-server。\n# 复用本地 Codex 当前认证；没有自动 provider 回退。\n# finite tools: spiceowl_inspect_agent_source / spiceowl_prepare_experiment\n# 已实现与 fake protocol 测试不构成真实模型推理证据。';
      break;
    case 1:
      view.input = {operation: 'inspect_agent_source', source: '<用户提供的原始源字节>', source_id: 'dut.scs', support_files: '<显式提供的相对文件映射>'};
      view.output = {requirements: {schema: 'spiceowl.netlist-model-requirements.v1', mode: 'agent-tb',
        source_sha256: null, interfaces: [{name: circuit.title, ports: circuit.ports}], required_masters: circuit.models},
        preserved_source_parameters: true, electrical_semantics_inferred_from_model_names: false};
      view.ui = [['源文件', '保留原始字节；显示摘要与完整源身份。'], ['接口', circuit.ports.join(' · ')],
        ['外部依赖', circuit.models.join(' · ')], ['模型可执行性', '只有引用名字，尚不能证明模型已经就绪。']];
      view.tools = 'from spiceowl.onboarding.agent_tb import inspect_agent_source\n\nrequirements = inspect_agent_source(\n    source_bytes, source_id="dut.scs",\n    support_files=provided_support_files,\n)\n\n# CircuitGit 对应入口：ic_onboarding_netlist\n# 只提供文本和显式相对支持文件，不提供任意远端文件路径。';
      break;
    case 2:
      view.input = {...bindings(), ssh_targets: ['pool-a', 'pool-b'], aliases_are_redacted_examples: true,
        resource_request: {cpu_count: null, memory_bytes: null}, setup_selection_id: null};
      view.output = {identity_verified: null, simulator: 'spectre', simulator_version: null,
        remote_platform: {architecture: null, distribution: null}, license_checkout: 'not_established_by_version_probe',
        required_remaining_cpu_fraction: 0.2, required_remaining_memory_fraction: 0.2, reservation: false,
        eligible_host_id: null};
      view.ui = [['服务器池', '仅展示显式允许的别名；当前页面没有探测。'], ['环境激活', '固定选择的 setup 及其观察哈希，不接受任意 shell。'],
        ['资源建议', '最新时间、内存/CPU 余量、排除原因分别显示。'], ['工具与平台', '远端 Spectre 版本 / 架构 / 发行版；版本正常不证明许可证 checkout。']];
      view.tools = tool('ic_onboarding_ssh_hosts → ic_onboarding_probe', view.input);
      break;
    case 3:
      view.input = {...bindings(), lookup_path: cache, requested_model_names: circuit.models,
        library_id: null, entry_id: null, section_ids: [], sections: circuit.sections,
        tmi_relative_library: bootstrap ? 'RH_64/libTMImodel.so' : null};
      view.output = {lookup_path: cache, remote_scan_requested_by_this_path: cache === 'first_scan',
        coverage: '必须读取原始库的 coverage / missing', retained_library_id: null,
        same_entry_sections: circuit.sections, selected_tmi_id: null, fresh_audit_required: true,
        ready_to_run: false, measured_lookup_latency_ms: null};
      view.ui = [['查询路径', cache === 'cached' ? '读取已有 SQLite LUT；匹配主机、完整根目录、库 ID 与指纹。' : '显式触发授权根目录内的有界扫描，保存实际覆盖与缺失。'],
        ['同入口组合', circuit.sections ? circuit.sections.join(' → ') : '反相器需要自己的当前库证据与 section 选择，不能沿用另一 intake。'],
        ['TMI', '同一 ABI 下各目录必须有兼容的观测证据；冲突选项不能被自动选中。'], ['速度', '缓存路径减少远程扫描；本页不提供延迟实测值。']];
      view.tools = cache === 'cached' ? 'from spiceowl.onboarding.pdk_scope import (\n    library_scope_catalog, project_library_scopes,\n)\n\n# 读取 exact cache_id 绑定的原始 library；不重新扫描。\n# CircuitGit: ic_onboarding_pdk_scopes\n#             ic_onboarding_select_pdk_scope\n# 所有 ID / revision 从当前回执逐项复制。' : tool('ic_onboarding_discover_pdk', {...bindings(), probe_ids: ['<本次 probe_id>'], refresh: true});
      break;
    case 4:
      view.input = {...bindings(), helper_needed_for_this_case: bootstrap ? false : null,
        optional_helper_shape: {helper_files: {'behavior/example.va': '<经审核的原创 helper 字节>'}},
        standard_header_names: ['constants.vams', 'disciplines.vams'], veriloga_header_id: null};
      view.output = {helper_byte_hashes: null, standard_header_closure_sha256: null,
        fresh_header_audit_required: true, staging_owner: 'existing Runner',
        pdk_body_published: false, standard_header_body_in_public_archive: false,
        use_in_this_case: bootstrap ? 'not_applicable' : 'not_evidenced_by_published_checkpoint'};
      view.ui = [['使用条件', '有实际需要并声明完整 helper 时才进入该分支；不是默认给电路加行为模型。'],
        ['文件来源', 'DUT 支持文件 / TB-only helper / 工具标准头分别绑定。'], ['远端 staging', '标准头闭包经观察与重审，保持文件哈希；公开归档排除其正文。']];
      if (bootstrap) view.ui.unshift(['本例状态', '已审公开 TB 无 ahdl_include；not_applicable。下面只说明其他实验的可选 helper 分支。']);
      view.tools = '// 可选的受审 TB 文本形状（不是本次实际 helper）：\nahdl_include "behavior/example.va"\n\n// 工具标准头的有限观察：\nic_onboarding_veriloga_headers\n// roots 仅在私有配置内，使用已观察的 probe_id。\n// constants.vams / disciplines.vams 不能冒充自编 helper 上传。';
      break;
    case 5:
      view.input = {...bindings(), schema: 'ic.agent_testbench.v1',
        interface: {subckt: circuit.title, instance: 'XDUT', port_bindings: '<与原始端口逐一匹配>'},
        experiment_structure: bootstrap ? {gate_drive: 'VBST', external_sampling_device: 'MSAMPLE', hold_element: 'CHOLD', sampled_node: 'VOUT'} : {input: 'CLK', output: 'CLKN', rails: ['VDD', 'VSS']},
        conditions: bootstrap ? {supply_v: PUBLIC_EVIDENCE.testbench.supplyV,
          stimulus_frequency_hz: PUBLIC_EVIDENCE.testbench.inputHz, load_f: PUBLIC_EVIDENCE.testbench.holdCapacitanceF,
          temperature_c: PUBLIC_EVIDENCE.testbench.temperatureC, source: 'reviewed_publication_testbench'} : {supply_v: null, stimulus_frequency_hz: null, load: null, temperature: null},
        sampling: bootstrap ? {points: 4096, coherent_cycles_k: PUBLIC_EVIDENCE.testbench.coherentBin,
          sample_rate_hz: PUBLIC_EVIDENCE.testbench.sampleRateHz, fundamental_relation: 'fin = k * fs / 4096',
          warmup_cycles: PUBLIC_EVIDENCE.testbench.warmupCycles, first_sample_s: PUBLIC_EVIDENCE.testbench.firstSampleS,
          last_sample_s: PUBLIC_EVIDENCE.testbench.lastSampleS} : null,
        criteria: [], criteria_note: bootstrap ? '冻结 TB 没有数值验收阈值；后续指标只作报告，不能产生达标结论。' : '反相器实验条件尚未确定；空列表不能产生通过结论。'};
      view.output = {draft_id: null, manifest_sha256: null, static_validation: '以原始回执为准',
        source_bytes_preserved: true, inferred_supply_or_dimensions: false, run_result: null};
      view.ui = bootstrap ? [['采样链路', 'VIN → 外接 MSAMPLE → 保持节点 / CHOLD。VBST 仅连接采样管的 gate drive。'],
        ['4096 点相干契约', '冻结 fs、整数周期数 k、预热与采样相位；从实际时间轴验证，不靠画面判断。'],
        ['已审测试条件', 'VDD = 1 V；fs = 1 GHz；fin = 442138671.875 Hz；k = 1811；CHOLD = 100 fF。完整参数和源字节见下方公开材料。'],
        ['验收限制', '没有数值验收阈值；这些是待审实验条件，没有对应实测指标。']] : [['反相器接口', 'CLK 驱动两个栅极，CLKN 是共漏输出。'],
        ['独立实验条件', '显式电源、刺激、输出负载、保存信号和阈值；不继承 Bootstrap 的条件。'], ['当前限度', '本页仅有公开结构，未提供该反相器的本次运行或审核快照。']];
      view.tools = tool('ic_onboarding_agent_tb_draft', {intake_id: null, revision: null,
        testbench: '<完整受审 ic.agent_testbench.v1 对象>', selection: '<当前 PDK / header 选择>'});
      break;
    case 6:
      view.input = {selection: '<本次缓存 scope selection>', audit: '<实际 fresh audit receipt>',
        audit_config: '<最终入口、sections、TMI 和 required_masters 的原配置>'};
      view.output = {comparison_status: null, expected_success_status: 'matched', missing: null,
        checked_evidence: ['file hashes', 'dependency scopes', 'full model namespace', 'TMI pins'],
        remote_freshness_verified_by_pure_comparison: false, ready_to_run: false};
      view.ui = [['文件 pins', '比较实际 audit 文件集合与每个哈希，不能仅核对入口网表。'],
        ['namespace', '重复/冲突声明、缺模型、作用域或端口证据不一致均阻断。'], ['新鲜度与执行权', '由执行所有者核对来源、主机、时间和审核；纯比较函数没有执行授权。']];
      view.tools = 'from spiceowl.onboarding.pdk_scope import verify_scope_selection_audit\n\ncomparison = verify_scope_selection_audit(\n    selection, fresh_audit, audit_config=original_audit_config,\n)\n\n# CircuitGit 在准备受审 TB 时调用依赖审计。\n# ic_onboarding_agent_tb_prepare 不是 solver 运行。';
      break;
    case 7:
      view.input = {intake_id: null, revision: bootstrap ? 10 : null, review_id: null,
        frozen_materials: ['DUT / TB / helper bytes', 'conditions', 'PDK / TMI / header pins',
          'resource scope', 'save outputs', 'measurement criteria'], display_is_approval: false};
      view.output = {published_case_state: bootstrap ? CHECKPOINT.state : 'no_case_receipt_published',
        published_case_revision: bootstrap ? CHECKPOINT.revision : null,
        note: '以上检查点已单独标识；其余字段仍为形状示例。', human_decision: null,
        solver_result: null, measurement_result: null, page_can_approve: false};
      view.ui = [['已知检查点', bootstrap ? 'revision 10 / awaiting_human_review：等待真实审核流程中的人类决定。' : '该反相器仅展示公开结构，没有引用 Bootstrap 的审核身份。'],
        ['审核材料', '源、TB、条件、依赖、资源和输出构成同一个冻结对象。'], ['允许的下一步', '阅读原 review；由现有宿主的人类审核表单处理决定。']];
      view.tools = '// 读取准确的冻结审核：\nic_onboarding_agent_tb_review\n\n// 需要宿主人工审核时沿用现有入口：\nic_request_review\n\n// request_id / fingerprint 只能来自原始待审请求。\n// 禁止加入 approved:true / confirmed:true 来替代人工决定。';
      break;
    case 8:
      view.input = {intake_id: null, revision: null, plan_id: null, original_submission_key: null,
        authorization: '只接受实际执行所有者认可的原始审核/授权', simulator: 'spectre', start_virtuoso: false};
      view.output = {job_id: null, run_id: null, execution: 'not_observed_in_published_case',
        measurement_validity: 'not_evaluated', functional_performance: 'not_evaluated',
        operating_qualification: 'not_evaluated', dependency_provenance: 'requires_original_receipts',
        automatic_resubmit_on_disconnect: false};
      view.ui = [['执行', '当前无可公开的本次 solver 回执。'], ['数据有效性', '尚无本次原始结果可解析。'],
        ['功能表现', '未测量；不能显示 pass。'], ['工作区 / 工作条件', '依赖明确的模型条件、保存数据和判据。'], ['依赖来源', '保留原始 source / audit / run 身份，不随连接重建而变化。']];
      view.tools = '// 仅在实际审核通过且返回动作就绪后：\nic_onboarding_run\n\n// 发送后响应丢失：查询原始提交，不改 key 重发。\nic_onboarding_submission\n\n// 明确观察/收集同一任务：\nic_onboarding_inspect';
      break;
    case 9: {
      const spectral = operation === 'spec_analyze' || operation === 'coherent_fft';
      const metrics = spectral ? ['sfdr_db', 'sndr_db', 'enob_bits'] : operation === 'ac_metrics' ? ['unity_gain_bandwidth_hz', 'phase_margin_deg'] : operation === 'dc_op_metrics' ? ['saturation_unknown_count', 'static_power_w'] : ['settling_s', 'delay_s', 'average_power_w'];
      view.input = {...bindings(), job_id: null, dataset_id: null, operation,
        inputs: {saved_signal_or_device_ids: '<仅来自该run的catalog>'},
        parameters: {backend: 'spiceowl', ...(spectral ? {planned_sample_count: bootstrap ? 4096 : null,
          sample_rate_hz: bootstrap ? PUBLIC_EVIDENCE.testbench.sampleRateHz : null,
          fundamental_hz: bootstrap ? PUBLIC_EVIDENCE.testbench.inputHz : null, window: 'rectangular',
          declared_sampling_window: bootstrap ? {first_s: PUBLIC_EVIDENCE.testbench.firstSampleS, last_s: PUBLIC_EVIDENCE.testbench.lastSampleS,
            interpolation_allowed: false, sample_mismatch: 'stop_with_data_gap_no_implicit_crop'} : null} :
          operation === 'ac_metrics' ? {response_kind: null, feedback_convention: null, phase_reference_deg: null} :
          operation === 'dc_op_metrics' ? {device_contracts: null, region_mapping_source: null} :
          {initial: null, final: null, step_time_s: null, settling_band: null, current_sign: null})},
        note: '选项形状需按具体算法补齐；本例不是可执行请求。'};
      view.output = {operation, measurement_status: 'not_run', backend_version: null,
        original_run_and_data_hashes: null, metrics: Object.fromEntries(metrics.map(name => [name, {value: null, status: 'not_measured'}])),
        specification_result: 'not_evaluated', synthetic_performance_numbers: false};
      view.ui = [['数据入口', '先有同一原始 run 的归档、数据集、信号和时间/频率轴，再算指标。'],
        ['分析契约', spectral ? '矩形窗相干 FFT 要检验整数周期；4096 点是计划，实际记录仍要校验。' : operation === 'ac_metrics' ? 'Bode、UGBW、PM 使用真实复数 AC 数据和显式反馈约定。' : operation === 'dc_op_metrics' ? '原始 region 数字没有通用含义；按模型明确工作区映射或 Vdsat 判据。' : '显式初值、终值、窗与阈值；电流方向和供电信号决定功率含义。'],
        ['结果', '本次未提供数据，所有性能数字保持 null。算法可用不等于本次已经运行。']];
      view.tools = `// CircuitGit：绑定原始 job / dataset / signal ID\nic_onboarding_analysis\n\n// 独立 SDK 对显式提供的数据执行算法：\nfrom spiceowl.adctools import analyze\nreport = analyze("${operation}", complete_explicit_parameters)\n\n// CLI（完整参数文件准备好后，由操作者调用）：\npython -m spiceowl.adctools ${operation} --request request.json --output report.json\n\n// 上游 backend="adctoolbox" 仅用于它支持的操作，\n// 必须保留实际版本与参数；缺包/不支持时不能静默替换。`;
      break;
    }
  }
  view.input = example(view.input); view.output = example(view.output);
  view.publishedEvidence = publishedStageEvidence(index, circuitKey);
  if (!bootstrap && index === 5) view.description = '为公开的两管反相器另行声明电源、刺激、负载、保存信号和测量条件。保留原始 M9 / M10 参数；不把另一电路的测试条件或审核回执转给它。';
  if (!bootstrap && index === 7) view.description = '反相器示例用于说明相同的冻结审核契约。本页没有该电路的本次待审回执，不能沿用 Bootstrap 的 revision 10 或任何执行身份。';
  return view;
}

export function publishedStageEvidence(index, circuit = 'bootstrap') {
  if (index === 0) return '双模式接口已实现，fake protocol 测试通过。Standalone 显式 Connect 才启动 Codex；嵌入模式复用 CircuitGit 会话。真实 provider 认证 / 推理状态仍为 unknown。';
  if (circuit !== 'bootstrap') return index === 1
    ? 'CMOS_INVERTER 的完整公开源已提供：M9 / M10、CLK / CLKN / VDD / VSS。没有这个反相器的 TB、模型选择、审核或运行回执。'
    : '这个反相器只有独立公开电路源。本阶段尚无独立实验材料或执行证据，不复用 Bootstrap 的条件、审计、revision 或结果。';
  return [null,
    '完整 Bootstrap DUT 包含 11 个 MOS 和 1 个电容，五个公开端口；@RNL 几何来自源内注释，映射完整度 partial。',
    '公开摘要记录远端 x86_64、CentOS/RHEL-family 的环境观察，用于 RH_64 TMI 选择。当前页面没有重新探测；动态加载兼容性及实际许可证 checkout 未证实。',
    '缓存目录有 3 个 scope 文件、108 个 scopes 和 91 个模型 namespace；同入口选择 pre_simu / TTMacro_MOS_MOSCAP / TT_MOM。cachedSelectionReadyToRun=false；没有毫秒性能基准。',
    '本例完整 TB 没有 ahdl_include，Verilog-A/helper 为 not_applicable。标准头发现、哈希和 staging 流程保留给确实需要的其他实验。',
    '冻结 TB：VDD=1 V，VIN=0.5±0.45 V，fs=1 GHz，fin=442138671.875 Hz（1811/4096·fs）。MSAMPLE 由 VBST 驱动，CHOLD=100 fF；预热 64 周期，4096 点，27 °C。没有数值验收阈值。',
    'Fresh static audit：4 个文件（含动态库）、108 个 scopes、91 个 namespace 模型；4 个必需模型全部匹配，missing=0，unknownDirectives=0；selection 比较 matched。仅覆盖支持的静态语法，不证明执行或工艺资格。',
    '实际检查点：revision 10 / awaiting_human_review。冻结 +mt=1，池准入最低可用内存 512 MiB；内存硬上限和 wall-time 硬上限均未设置。池准入不是操作系统资源限制。',
    'planned：Bootstrap 求解未启动，没有 job、waveform 或 solver 完成证据。断线后的自动重提和自动数值修复不是当前已实现能力。',
    'planned：已冻结 VOUT 频谱、VIN 参考、矩形窗与 4096 点条件；首点 64.75 ns，末点 4.15975 µs。实际数据缺失，FFT/SFDR/SNDR/ENOB 都未测量；缺点或多点必须停下，不插值或暗中裁切。',
  ][index];
}

const SVG_STYLE = 'fill="none" stroke="#26745f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
function diagram(key) {
  if (key === 'inverter') return `<svg viewBox="0 0 560 278" role="img" aria-labelledby="inverter-title"><title id="inverter-title">公开两管反相器连接：CLK共栅，CLKN共漏，M10源体接VDD，M9源体接VSS。</title><g ${SVG_STYLE}><path d="M282 28v40m0 42v63m0 42v35M90 140h110m0-55v108m0-108h38m-38 108h38m44-53h150"/><path d="M253 68v43m0 62v43m10-148v43m0 62v43m0-130h19v-18m-19 42h19m-19 82h19v-20m-19 42h19m-34-1v-40m-5-105v43M274 251h16m-13 6h10m-8 6h6"/><circle cx="243" cy="85" r="5" fill="#fbfcf9"/><path d="M282 68h46V28h-46m0 187h46v35h-46" stroke="#78998b" stroke-dasharray="4 4"/></g><g fill="#24423e" font-family="system-ui,sans-serif" font-size="12"><text x="261" y="19">VDD</text><text x="57" y="145">CLK</text><text x="440" y="145">CLKN</text><text x="345" y="86">M10 · PMOS</text><text x="345" y="106" font-size="10" fill="#638174">pch_ulvt_mac</text><text x="345" y="193">M9 · NMOS</text><text x="345" y="213" font-size="10" fill="#638174">nch_ulvt_mac</text><text x="264" y="276">VSS</text></g><circle cx="282" cy="140" r="4" fill="#26745f"/></svg>`;
  return `<svg viewBox="0 0 560 278" role="img" aria-labelledby="bootstrap-title"><title id="bootstrap-title">用户指定的测试结构：Bootstrap输出VBST控制外接MSAMPLE，在CHOLD保持节点取样；数值和精确端序由受审TB提供。</title><g ${SVG_STYLE}><rect x="65" y="30" width="238" height="90" rx="10" fill="#f1f6ef"/><path d="M24 64h41m-41 33h41m238-29h102v76M24 175h245m94 0h104v34m0 19v22"/><rect x="269" y="147" width="94" height="55" rx="6" fill="#fbfcf9"/><path d="M405 144h-89v3m137 62h29m-29 8h29m-22 34h15m-12 6h9m-7 6h5M47 175V97h18"/></g><g fill="#24423e" font-family="system-ui,sans-serif"><text x="100" y="65" font-size="13" font-weight="650">Bootstrap gate driver</text><text x="95" y="87" font-size="9" fill="#658276">RS9_L5_9bit_Bootstrap</text><text x="8" y="53" font-size="10">CLK</text><text x="8" y="91" font-size="10">VIN</text><text x="331" y="59" font-size="11">VBST</text><text x="279" y="179" font-size="11">MSAMPLE</text><text x="21" y="195" font-size="11">VIN</text><text x="416" y="166" font-size="10">保持节点</text><text x="489" y="216" font-size="10">CHOLD</text><text x="107" y="110" font-size="9" fill="#658276">VDD / VSS：由实验条件绑定</text><text x="158" y="257" font-size="10" fill="#658276">4096 点相干采样是目标契约，不是已有数据</text></g><circle cx="467" cy="175" r="4" fill="#26745f"/></svg>`;
}

export function mountWorkflow(doc, locationObject = globalThis.location, historyObject = globalThis.history) {
  const state = {index: 0, tab: 'input', circuit: 'bootstrap', mode: 'embedded_in_circuitgit', cache: 'cached', analysis: 'spec_analyze', source: 'bootstrap-dut'};
  const byId = id => doc.getElementById(id);
  const buttons = Array.from(doc.querySelectorAll('[data-tab]'));
  const parsedStage = /^#stage-(10|[1-9])$/.exec(locationObject?.hash || '');
  if (parsedStage) state.index = Number(parsedStage[1]) - 1;
  const stageList = byId('stage-list');
  STAGES.forEach((stage, index) => {
    const li = doc.createElement('li'), button = doc.createElement('button');
    button.type = 'button'; button.dataset.stage = String(index);
    const number = doc.createElement('span'), copy = doc.createElement('span'), title = doc.createElement('strong'), small = doc.createElement('small');
    number.className = 'nav-number'; number.textContent = String(index + 1).padStart(2, '0');
    copy.className = 'nav-copy'; title.textContent = stage.short; small.textContent = stage.subtitle;
    copy.append(title, small); button.append(number, copy); li.append(button); stageList.append(li);
    button.addEventListener('click', () => selectStage(index));
    button.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % STAGES.length;
      if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index + STAGES.length - 1) % STAGES.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = STAGES.length - 1;
      if (next !== undefined) { event.preventDefault(); selectStage(next); stageList.querySelector(`[data-stage="${next}"]`).focus(); }
    });
  });
  const rows = byId('responsibility-rows');
  RESPONSIBILITIES.forEach(values => {
    const tr = doc.createElement('tr');
    values.forEach((value, index) => { const cell = doc.createElement(index === 0 ? 'th' : 'td'); if (index === 0) cell.scope = 'row'; cell.textContent = value; tr.append(cell); });
    rows.append(tr);
  });
  function selectStage(index) {
    state.index = index; render();
    if (historyObject?.replaceState) historyObject.replaceState(null, '', `#stage-${index + 1}`);
    byId('navigation-status').textContent = `第 ${index + 1} 阶段：${STAGES[index].title}`;
  }
  function fact(label, text) {
    const el = doc.createElement('div'), span = doc.createElement('span'), p = doc.createElement('p');
    el.className = 'fact'; span.textContent = label; p.textContent = text; el.append(span, p); return el;
  }
  function renderSource() {
    const available = PUBLICATION_SOURCES.filter(source => source.id === 'source-manifest' || source.id.startsWith(state.circuit === 'bootstrap' ? 'bootstrap-' : 'inverter-'));
    if (!available.some(source => source.id === state.source)) state.source = available[0].id;
    const selector = byId('publication-source');
    selector.replaceChildren(...available.map(source => { const option = doc.createElement('option'); option.value = source.id; option.textContent = source.filename; return option; }));
    selector.value = state.source;
    const source = available.find(source => source.id === state.source);
    byId('publication-source-code').textContent = source.text;
    byId('source-heading').textContent = `${source.filename} · 完整公开副本`;
    byId('source-download').href = `./${source.filename}`;
    byId('source-download').download = source.filename;
    const identities = [fact('公开副本 SHA-256', source.publicationSha256)];
    if (source.originalSourceSha256) identities.push(fact('原始源 SHA-256', source.originalSourceSha256));
    identities.push(fact('来源处理', source.transformation || '逐项列出原始来源、公开副本与变换；下载清单字节与授权材料保持一致。'));
    byId('source-identity').replaceChildren(...identities);
  }
  function render() {
    const view = stageView(state.index, state), circuit = CIRCUITS[state.circuit];
    byId('circuit-note').textContent = circuit.note;
    byId('circuit-diagram').innerHTML = diagram(state.circuit); // Only authored static SVG strings, never imported source or user text.
    byId('circuit-caption').textContent = circuit.caption;
    byId('checkpoint-title').textContent = state.circuit === 'bootstrap' ? 'revision 10 · 等待人工审核' : 'CMOS Inverter · 仅公开结构示例';
    byId('checkpoint-code').textContent = state.circuit === 'bootstrap' ? CHECKPOINT.state : 'structure_only';
    byId('checkpoint-detail').textContent = state.circuit === 'bootstrap' ? '已审公开 DUT、完整 TB 和静态审计摘要；没有 job、求解、波形或 FFT 结果。步骤 9 / 10 为 planned。JSON 标签内仍是契约说明，不是服务回执。' : '没有该反相器的本次审核或运行回执；不能沿用 Bootstrap 的条件、revision 或结果。';
    byId('stage-number').textContent = String(state.index + 1).padStart(2, '0');
    byId('stage-kicker').textContent = view.tag; byId('stage-title').textContent = view.title;
    byId('stage-status').textContent = view.status; byId('stage-status').className = `badge ${view.statusKind}`;
    byId('stage-description').textContent = view.description;
    byId('stage-evidence-detail').textContent = view.publishedEvidence;
    renderSource();
    byId('stage-facts').replaceChildren(fact('AI 做什么', view.ai), fact('确定性工具做什么', view.engine));
    byId('mode-controls').hidden = state.index !== 0; byId('cache-controls').hidden = state.index !== 3; byId('analysis-controls').hidden = state.index !== 9;
    byId('stage-guard').textContent = view.guard; byId('stage-position').textContent = `${state.index + 1} / ${STAGES.length}`;
    byId('previous-stage').disabled = state.index === 0; byId('next-stage').disabled = state.index === STAGES.length - 1;
    for (const button of stageList.querySelectorAll('[data-stage]')) {
      if (Number(button.dataset.stage) === state.index) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
    }
    for (const button of doc.querySelectorAll('[data-circuit]')) button.setAttribute('aria-pressed', String(button.dataset.circuit === state.circuit));
    buttons.forEach(button => { const active = button.dataset.tab === state.tab; button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1; });
    byId('detail-panel').setAttribute('aria-labelledby', `tab-${state.tab}`);
    byId('detail-label').textContent = state.tab === 'tools' ? '调用形状说明 · 不执行 · 必须补齐原始身份' : state.tab === 'ui' ? '界面状态说明 · 未连接实时服务' : '结构示例 · 不发送 · 非服务回执';
    byId('detail-kind').textContent = state.tab === 'tools' ? 'TOOLS / SDK / CLI' : state.tab === 'ui' ? 'STATE' : 'JSON';
    byId('detail-code').hidden = state.tab === 'ui'; byId('detail-ui').hidden = state.tab !== 'ui';
    if (state.tab === 'ui') {
      const elements = view.ui.map(([label, text]) => { const item = doc.createElement('div'), mark = doc.createElement('span'), p = doc.createElement('p'), strong = doc.createElement('strong'); item.className = 'ui-state'; mark.className = 'ui-mark'; mark.setAttribute('aria-hidden', 'true'); strong.textContent = label + ' · '; p.append(strong, doc.createTextNode(text)); item.append(mark, p); return item; });
      if (view.empty) { const empty = doc.createElement('div'); empty.className = 'empty-evidence'; empty.textContent = view.empty; const small = doc.createElement('small'); small.textContent = '不显示虚构的波形或性能数字。'; empty.append(small); elements.push(empty); }
      byId('detail-ui').replaceChildren(...elements);
    } else byId('detail-code').textContent = state.tab === 'tools' ? view.tools : JSON.stringify(view[state.tab], null, 2);
  }
  buttons.forEach((button, index) => {
    button.addEventListener('click', () => { state.tab = button.dataset.tab; render(); });
    button.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % buttons.length;
      if (event.key === 'ArrowLeft') next = (index + buttons.length - 1) % buttons.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = buttons.length - 1;
      if (next !== undefined) { event.preventDefault(); state.tab = buttons[next].dataset.tab; render(); buttons[next].focus(); }
    });
  });
  doc.querySelectorAll('[data-circuit]').forEach(button => button.addEventListener('click', () => { state.circuit = button.dataset.circuit; render(); }));
  doc.querySelectorAll('input[name="mode"], input[name="cache"]').forEach(input => input.addEventListener('change', () => { if (input.checked) { state[input.name] = input.value; render(); } }));
  byId('analysis-operation').addEventListener('change', event => { state.analysis = event.target.value; render(); });
  byId('publication-source').addEventListener('change', event => { state.source = event.target.value; renderSource(); });
  byId('previous-stage').addEventListener('click', () => { if (state.index > 0) selectStage(state.index - 1); });
  byId('next-stage').addEventListener('click', () => { if (state.index < STAGES.length - 1) selectStage(state.index + 1); });
  doc.defaultView?.addEventListener('hashchange', () => {
    const match = /^#stage-(10|[1-9])$/.exec(locationObject?.hash || '');
    if (match) { state.index = Number(match[1]) - 1; render(); }
  });
  render();
  return {getState: () => ({...state})};
}

if (typeof document !== 'undefined') mountWorkflow(document);
