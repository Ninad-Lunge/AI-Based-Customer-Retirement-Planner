import yfinance as yf
import pandas as pd
import numpy as np
import os
from sklearn.preprocessing import MinMaxScaler
from keras.models import Sequential
from keras.layers import LSTM, Dense
import joblib
from nsetools import Nse
import urllib.error

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

model_dir = "saved_models"

if not os.path.exists(model_dir):
    os.makedirs(model_dir)

def fetch_nifty50_tickers():
    nse = Nse()
    try:
        nifty50_stocks = nse.get_top_gainers()[:25]
        tickers = [stock['symbol'] for stock in nifty50_stocks]
        tick = [yf.Ticker(ticker+".NS") for ticker in tickers]
        print(tick)
        return tick
    except urllib.error.HTTPError as e:
        print(f"HTTPError: {e}")
        return []

def fetch_stock_data(tickers):
    stock_data = []
    for ticker in tickers:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5y")  # Last 5 years of data
        if not hist.empty:
            model_path = os.path.join(model_dir, f"{ticker}_lstm.pkl")
            scaler_path = os.path.join(model_dir, f"{ticker}_scaler.pkl")

            if os.path.exists(model_path) and os.path.exists(scaler_path):
                model = joblib.load(model_path)
                scaler = joblib.load(scaler_path)
            else:
                data = hist[['Close']].values
                scaler = MinMaxScaler(feature_range=(0,1))
                data_scaled = scaler.fit_transform(data)
                X_train = []
                y_train = []
                for i in range(60, len(data_scaled)):
                    X_train.append(data_scaled[i-60:i, 0])
                    y_train.append(data_scaled[i, 0])
                X_train, y_train = np.array(X_train), np.array(y_train)
                X_train = np.reshape(X_train, (X_train.shape[0], X_train.shape[1], 1))
                
                model = Sequential()
                model.add(LSTM(units=50, return_sequences=True, input_shape=(X_train.shape[1], 1)))
                model.add(LSTM(units=50))
                model.add(Dense(1))

                model.compile(optimizer='adam', loss='mean_squared_error')
                model.fit(X_train, y_train, epochs=1, batch_size=32)

                joblib.dump(model, model_path)
                joblib.dump(scaler, scaler_path)

            data = scaler.transform(hist[['Close']])
            X_test = []
            for i in range(60, len(data)):
                X_test.append(data[i-60:i, 0])
            X_test = np.array(X_test)
            X_test = np.reshape(X_test, (X_test.shape[0], X_test.shape[1], 1))
            predicted_stock_price = model.predict(X_test)
            predicted_stock_price = scaler.inverse_transform(predicted_stock_price)
            hist['Predicted'] = np.nan
            hist.iloc[60:, hist.columns.get_loc('Predicted')] = predicted_stock_price.flatten()   

            annual_return = hist['Predicted'].pct_change().mean() * 252 * 100
            volatility = hist['Predicted'].pct_change().std() * np.sqrt(252) * 100
            beta = stock.info.get('beta', 1)
            sharpe_ratio = annual_return / volatility
            risk_profile = 'Low' if volatility < 7 else 'Medium' if volatility < 10 else 'High'

            stock_data.append({
                'Stock Name': ticker,
                'Annual Return (%)': round(annual_return, 2),
                'Volatility (%)': round(volatility, 2),
                'Beta': round(beta, 2),
                'Sharpe Ratio': round(sharpe_ratio, 2),
                'Risk Profile': risk_profile
            })
    return pd.DataFrame(stock_data)

def suggest_investment(df, years, target_fund, monthly_investment, risk_category):
    df = df.copy()

    if risk_category == "high":
        df = df[(df['Annual Return (%)'] > 20) & (df['Volatility (%)'] > 10)]
    elif risk_category == "medium":
        df = df[(df['Annual Return (%)'] > 10) & (df['Annual Return (%)'] <= 20) & (df['Volatility (%)'] <= 10) & (df['Volatility (%)'] >= 7)]
    elif risk_category == "low":
        df = df[(df['Annual Return (%)'] <= 10) & (df['Volatility (%)'] < 7)]
    else:
        return "Invalid risk category selected."

    if df.empty:
        return "No stocks available in the selected risk category."

    df['Priority Score'] = df['Sharpe Ratio'] / df['Volatility (%)']
    df = df.sort_values(by='Priority Score', ascending=False)

    n = years * 12
    P = monthly_investment
    target = target_fund
    required_annual_return = ((target / (P * ((1 + 0.01) * n - 1) / 0.01)) * (1/n)) - 1
    required_annual_return = required_annual_return * 12

    allocation = []
    for index, row in df.iterrows():
        if row['Annual Return (%)'] >= required_annual_return:
            allocation.append((row['Stock Name'], row['Annual Return (%)'], row['Risk Profile']))
        if len(allocation) >= 10:
            break

    if not allocation:
        best_stock = df.iloc[0]
        best_annual_return = best_stock['Annual Return (%)']
        future_value = P * ((((1 + best_annual_return / 100 / 12)**n) - 1) / (best_annual_return / 100 / 12))
        return f"It's not possible to achieve your retirement goal. The maximum possible future value with the best available stock ({best_stock['Stock Name']}) is {future_value:.2f} INR."

    future_values = []
    total_future_value = 0
    total_invested = P * n
    for stock, return_rate, risk in allocation:
        future_value = P * ((((1 + return_rate / 100 / 12)**n) - 1) / (return_rate / 100 / 12))
        future_values.append((stock, return_rate, risk, future_value))
    
    total_priority_score = sum(df.loc[df['Stock Name'].isin([stock for stock, _, _ in allocation]), 'Priority Score'])
    investment_percentages = [(stock, (df.loc[df['Stock Name'] == stock, 'Priority Score'].values[0] / total_priority_score) * 100) for stock, _, _ in allocation]
    for stock, percentage in investment_percentages:
        for future_stock, return_rate, risk, future_value in future_values:
            if stock == future_stock:
                total_future_value += future_value * (percentage / 100)
                break
    
    total_percentage = sum(percentage for _, percentage in investment_percentages)
    investment_percentages = [(stock, round(percentage / total_percentage * 100, 2)) for stock, percentage in investment_percentages]
    
    total_return_percentage = ((total_future_value - total_invested) / total_invested) * 100
    average_annual_return_percentage = (total_return_percentage / years)

    # Monte Carlo Simulation to estimate risk
    simulations = 100000
    final_values = []
    for _ in range(simulations):
        simulated_value = 0
        for stock, percentage in investment_percentages:
            stock_data = df[df['Stock Name'] == stock]
            annual_return = np.random.normal(stock_data['Annual Return (%)'].values[0], stock_data['Volatility (%)'].values[0])
            simulated_value += P * ((((1 + annual_return / 100 / 12)**n) - 1) / (annual_return / 100 / 12)) * (percentage / 100)
        final_values.append(simulated_value)
    
    risk_value_at_risk = np.percentile(final_values, 5)
    risk_value_at_risk_percentage = ((risk_value_at_risk - total_invested) / total_invested) * 100
    average_value = np.mean(final_values)

    investment_suggestions = []
    for stock, percentage in investment_percentages:
        for future_stock, return_rate, risk, future_value in future_values:
            if stock == future_stock:
                investment_suggestions.append({
                    'Stock Name': stock,
                    'Investment Percentage': percentage,
                    'Annual Return (%)': return_rate,
                    'Risk Profile': risk,
                    'Future Value (INR)': round(future_value, 2)
                })
                break

    investment_suggestions_df = pd.DataFrame(investment_suggestions)

    return {
        'Total Future Value (INR)': round(total_future_value, 2),
        'Investment Suggestions': investment_suggestions_df.to_dict(orient='records'),
        'Average Annual Return (%)': round(average_annual_return_percentage, 2),
        'Value at Risk (5th percentile) (INR)': round(risk_value_at_risk, 2),
        'Value at Risk (5th percentile) (%)': round(risk_value_at_risk_percentage, 2),
        'Average Value from Simulation (INR)': round(average_value, 2)
    }

# Fetch Nifty 50 tickers dynamically
nifty50_tickers = fetch_nifty50_tickers()

# List of additional tickers including gold, bonds, and indices
additional_tickers = [
    "GLD", "TLT", "IEF", "BND", "AGG", "LQD", "HYG", "TIP", "SHY", "MUB", "BIV",
    "^GSPC", "^DJI", "^IXIC", "^RUT", "^FTSE", "^N225", "^HSI", "^GDAXI", "^FCHI", "^STOXX50E"
]

# Combine all tickers
investment_tickers = nifty50_tickers + additional_tickers

# Fetch stock data and calculate metrics
df = fetch_stock_data(investment_tickers)
# print(df)

# # Example of suggesting an investment
# years = 10
# target_fund = 1000000  # Target fund in INR
# monthly_investment = 10000  # Monthly investment in INR
# risk_category = 'medium'  # Risk category: 'low', 'medium', 'high'

# investment_suggestion = suggest_investment(df, years, target_fund, monthly_investment, risk_category)
# print(investment_suggestion)
