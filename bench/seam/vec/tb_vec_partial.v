// The vector version of the seam experiment: an 8-bit bus, not a single enable.
//
// `src` counts 0..255 so that every bit of it toggles a DIFFERENT number of times
// (bit0 255, bit1 127, ... bit7 1). That is deliberate: under a bit reorder or a
// shift, the total number of toggles is unchanged and every bit still toggles, so
// a count-based or a lit/dark check sees nothing. Only the per-bit profile moves.
`timescale 1ns/1ps
module tb_vec_partial;
    reg clk = 0, rst = 1, en = 0, rd_en = 0;
    reg [7:0] src = 0;
    wire [7:0] seam_data;
    wire [7:0] rd_data;
    wire [2:0] count;
    wire full, empty;

    sync_fifo #(.WIDTH(8), .DEPTH(4)) u_fifo(
        .clk(clk), .rst(rst), .wr_en(en), .wr_data(seam_data),
        .rd_en(rd_en), .rd_data(rd_data), .full(full), .empty(empty), .count(count));

    assign seam_data = {src[7:4], 4'b0};    /* upper nibble only */

    always #5 clk = ~clk;
    always @(posedge clk) if (!rst) src <= src + 8'd1;

    initial begin
        repeat (2) @(posedge clk);
        rst = 0; en = 1;
        repeat (255) @(posedge clk);
        $display("TB_DONE");
        $finish;
    end
endmodule
