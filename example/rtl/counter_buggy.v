module counter_buggy #(
    parameter WIDTH = 8
)(
    input wire clk,
    input wire rst_n,
    output reg [WIDTH-1:0] count
);

    reg loop_sig_reg;
    wire loop_sig;

    always @(posedge clk or negedge rst_n)
        if (!rst_n)
            loop_sig_reg <= 1'b0;
        else
            loop_sig_reg <= loop_sig_reg ^ count[0];

    assign loop_sig = loop_sig_reg;

    always @(posedge clk or negedge rst_n)
        if (!rst_n)
            count <= {WIDTH{1'b0}};
        else
            count <= count + 1'b1;

endmodule