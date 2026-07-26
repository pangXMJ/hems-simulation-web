import hems_basecontrol

df_history, df_warning = hems_basecontrol.run_simulation(
    mode='baseline',
    outage_start_step=48,  # 12:00
    outage_end_step=60,    # 15:00
)

df_history.to_csv("Baseline_With_Outage_Test.csv", index=False, encoding='utf-8-sig')
print(df_history[['Time', '供電模式']].iloc[48:64])  # 看 12:00~16:00 之間有沒有變化