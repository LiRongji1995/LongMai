// INFRASTRUCTURE SMOKE TEST ONLY
// NOT A TRADING STRATEGY
// NO PROFITABILITY CLAIM

using QuantConnect.Algorithm;
using QuantConnect;
using QuantConnect.Data;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Orders.Slippage;
using QuantConnect.Securities;

namespace QuantSystem.Lean;

public sealed class Phase01DeterministicSmokeTest : QCAlgorithm
{
    private global::QuantConnect.Symbol _symbol = global::QuantConnect.Symbol.Empty;
    private int _validBars;

    public override void Initialize()
    {
        SetStartDate(2020, 1, 2);
        SetEndDate(2020, 1, 31);
        SetCash(1000m);
        var security = AddEquity("SYNTH", Resolution.Daily);
        _symbol = security.Symbol;
        security.SetFeeModel(new ConstantFeeModel(0m));
        security.SetSlippageModel(NullSlippageModel.Instance);
        security.SetFillModel(new ImmediateFillModel());
    }

    public override void OnData(Slice data)
    {
        if (!data.Bars.ContainsKey(_symbol))
        {
            return;
        }

        _validBars++;
        if (_validBars == 3)
        {
            MarketOrder(_symbol, 10);
        }
        else if (_validBars == 8)
        {
            Liquidate(_symbol);
        }
    }
}
