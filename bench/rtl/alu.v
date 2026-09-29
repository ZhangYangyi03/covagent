// A small ALU with enough decode space that a lazy testbench misses states.
module alu #(parameter W = 8) (
    input  wire         clk,
    input  wire         rst,
    input  wire         en,
    input  wire [2:0]   op,
    input  wire [W-1:0] a,
    input  wire [W-1:0] b,
    output reg  [W-1:0] y,
    output reg          zero
);
    always @(posedge clk) begin
        if (rst) begin
            y <= 0; zero <= 1;
        end else if (en) begin
            case (op)
                3'd0: y <= a + b;                  // ADD
                3'd1: y <= a - b;                  // SUB
                3'd2: y <= a & b;                  // AND
                3'd3: y <= a | b;                  // OR
                3'd4: y <= a ^ b;                  // XOR
                3'd5: y <= a << b[2:0];            // SHL
                3'd6: y <= (a < b) ? {{(W-1){1'b0}}, 1'b1} : 0;  // LT
                3'd7: y <= ~a;                     // NOT
                default: y <= a;                   // unreachable-ish default
            endcase
            zero <= (y == 0);
        end
    end
endmodule
