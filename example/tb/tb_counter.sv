module tb_counter;
    logic       clk;
    logic       rst_n;
    logic [7:0] count;

    counter u_dut (
        .clk  (clk),
        .rst_n(rst_n),
        .count(count)
    );

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    initial begin
        rst_n = 0;
        #20 rst_n = 1;
        #200 rst_n = 0;
        #20 rst_n = 1;
        #500 $finish;
    end

    always @(posedge clk) begin
        if (count > 8'd100)
            $display("count exceeded 100: %d", count);
    end
endmodule