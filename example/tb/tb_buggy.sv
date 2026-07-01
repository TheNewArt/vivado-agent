module tb_buggy;
    logic       clk;
    logic       rst_n;
    logic [7:0] count;

    counter_buggy u_dut (
        .clk  (clk),
        .rst_n(rst_n),
        .count(count)
    );

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    initial begin
        $dumpfile("dump.vcd");
        $dumpvars(0, tb_buggy);
        rst_n = 1;  // BUG: no reset assertion — stays X
        #500 $finish;
    end

    always @(posedge clk) begin
        if (count > 8'd100)
            $display("ERROR: count exceeded 100 at %0t", $time);
    end
endmodule