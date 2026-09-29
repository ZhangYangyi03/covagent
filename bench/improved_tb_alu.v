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
        // Cover remaining opcodes
        drive(3'd2, 8'd10, 8'd20); // AND
        drive(3'd3, 8'd10, 8'd20); // OR
        drive(3'd4, 8'd10, 8'd20); // XOR
        drive(3'd5, 8'd8,  8'd2);  // SHL
        drive(3'd6, 8'd5,  8'd10); // LT
        drive(3'd7, 8'd255, 8'd0); // NOT
        drive(3'bxxx, 8'd0, 8'd0); // Default
        repeat (2) @(posedge clk);
        $display("TB_DONE");
        $finish;
    end
endmodule