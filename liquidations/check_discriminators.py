import hashlib

def get_discriminator(name):
    h = hashlib.sha256(f"global:{name}".encode("utf-8")).digest()
    return h[:8].hex()

instructions = [
    "flashBorrowReserveLiquidity",
    "flashRepayReserveLiquidity",
    "liquidateObligationAndRedeemReserveCollateral",
    "refreshObligation"
]

print("Standard Anchor Discriminators:")
for name in instructions:
    print(f"{name}: {get_discriminator(name)}")
    
print("\nObserved Discriminators:")
print("02da8aeb4fc91966")
print("218493e497c04859")
print("8c90fd150a4af803")
print("b1479abce2854a37")
