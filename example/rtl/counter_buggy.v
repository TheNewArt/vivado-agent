```diff
--- a/C:\Users\10621\Desktop\vivado-agent\example\rtl\counter_buggy.v
+++ b/C:\Users\10621\Desktop\vivado-agent\example\rtl\counter_buggy.v
@@ -... +... @@
-    // BUG 3: combinational loop
-    assign loop_sig = loop_sig ^ count[0];
+    // FIX: combinational loop - use a register instead
+    reg loop_sig_reg;
+    always @(posedge clk or negedge rst_n)
+        if (!rst_n)
+            loop_sig_reg <= 1'b0;
+        else
+            loop_sig_reg <= loop_sig_reg ^ count[0];
+    assign loop_sig = loop_sig_reg;
```