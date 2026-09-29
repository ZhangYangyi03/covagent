// A deliberately lazy testbench: it only exercises ADD and SUB.
// The coverage closure tool's job is to notice the other six opcodes are dark
// and generate stimulus that reaches them.
`timescale 1ns/1ps
module tb_alu;
    reg clk = 0, rst = 1, en = 0;
    reg [2:0] op = 0;
    reg [7:0] a = 0, b = 0;
    wire [7:0] y;
    wire zero;

    alu dut(.clk(clk), .rst(rst), .en(en), .op(op), .a(a), .b(b), .y(y), .zero(zero));

    always #5 clk = ~clk;

    task drive(input [2:0] o, input [7:0] aa, input [7:0] bb);
        begin
            @(posedge clk); op <= o; a <= aa; b <= bb; en <= 1;
            @(posedge clk);
        end
    endtask

    integer i;
    initial begin
        repeat (2) @(posedge clk);
        rst <= 0;
        // only ADD and SUB -- this is the coverage hole
        drive(3'd0, 8'd10, 8'd20);
        drive(3'd0, 8'd0,  8'd0);
        drive(3'd1, 8'd50, 8'd8);
        drive(3'd1, 8'd3,  8'd3);
        repeat (2) @(posedge clk);
        $display("TB_DONE");
        $finish;
    end
endmodule
